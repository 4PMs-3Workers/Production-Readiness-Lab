import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const root = resolve(__dirname, '../../..');
const python = process.env.FRONTEND_E2E_PYTHON ?? 'python';
const password = process.env.FRONTEND_E2E_PASSWORD ?? 'Fictional-e2e-password!42';
const origin = 'http://127.0.0.1:3101';
const harness = (action: string, ...args: string[]) =>
  execFileSync(python, ['tools/scripts/frontend_e2e_backend.py', action, ...args], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: 'src/backend' },
    encoding: 'utf8',
    timeout: 30_000,
  });

interface Fixture {
  id: string;
  date: string;
  nextDate: string;
  startsAt: string;
  location: string;
}

interface Inspection {
  appointmentStatus: string;
  startsAt: string;
  endsAt: string;
  location: string | null;
  durationMinutes: number;
  proposalVersion: number;
  proposals: {
    id: string;
    status: string;
    location: string | null;
    startsAt: string;
    endsAt: string;
  }[];
  audits: Record<string, number>;
  notifications: Record<string, number>;
}

const seed = () => JSON.parse(harness('seed-proposal-appointment')) as Fixture;
const inspect = (id: string) => JSON.parse(harness('inspect-proposals', '--id', id)) as Inspection;

async function login(page: Page, account: 'student' | 'staff' | 'other' | 'otherstaff' | 'admin') {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  if (account === 'staff' || account === 'otherstaff')
    await expect(page.getByRole('heading', { name: 'Lịch được giao' })).toBeVisible();
  else if (account === 'student' || account === 'other')
    await expect(page.getByRole('heading', { name: /Chào mừng,/ })).toBeVisible();
  else
    await expect(page.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
}

async function openStaff(page: Page, fixture: Fixture) {
  await page.goto(`/staff/appointments/${fixture.id}`);
  await expect(page.getByRole('heading', { name: 'Đề xuất đổi lịch', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Đề xuất đổi lịch', exact: true })).toBeEnabled();
}

async function reviewProposal(
  page: Page,
  fixture: Fixture,
  time = '11:00',
  location: 'keep' | 'clear' | string = 'keep',
) {
  await page.getByRole('button', { name: /^(Đề xuất đổi lịch|Thay thế đề xuất)$/ }).click();
  await page.getByLabel('Ngày đề xuất', { exact: true }).fill(fixture.nextDate);
  await page.getByLabel('Giờ đề xuất', { exact: true }).fill(time);
  await page
    .getByRole('combobox', { name: 'Thay đổi địa điểm', exact: true })
    .selectOption(location === 'keep' || location === 'clear' ? location : 'change');
  if (location !== 'keep' && location !== 'clear')
    await page.getByLabel('Địa điểm đề xuất', { exact: true }).fill(location);
  await page.getByRole('button', { name: 'Xem lại đề xuất', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Xác nhận gửi đề xuất', exact: true }),
  ).toBeVisible();
}

async function submitProposal(page: Page, fixture: Fixture, time = '11:00', location = 'keep') {
  await reviewProposal(page, fixture, time, location);
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === 'POST' &&
      r.url().endsWith(`/appointments/${fixture.id}/change-proposals`),
  );
  await page.getByRole('button', { name: 'Xác nhận gửi đề xuất', exact: true }).click();
  expect((await response).status()).toBe(201);
  await expect(page.getByRole('button', { name: 'Xác nhận gửi đề xuất', exact: true })).toHaveCount(
    0,
  );
  return inspect(fixture.id);
}

async function createViaApi(page: Page, fixture: Fixture, location: string) {
  const path = `/api/v1/staff/me/appointments/${fixture.id}/change-proposals`;
  const history = await page.request.get(path);
  expect(history.status()).toBe(200);
  const { context } = (await history.json()) as {
    context: { proposal_version: number; pending_proposal_id: string | null };
  };
  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.status()).toBe(200);
  const { csrf_token: token } = (await csrf.json()) as { csrf_token: string };
  const response = await page.request.post(path, {
    headers: { Origin: origin, 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() },
    data: {
      expected_proposal_version: context.proposal_version,
      expected_pending_proposal_id: context.pending_proposal_id,
      proposed_starts_at: `${fixture.nextDate}T12:00:00+07:00`,
      proposed_location: location,
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as { id: string };
}

async function decide(page: Page, action: 'accept' | 'decline') {
  await page
    .getByRole('button', {
      name: action === 'accept' ? 'Chấp nhận đề xuất' : 'Từ chối đề xuất',
      exact: true,
    })
    .click();
  await page
    .getByRole('button', {
      name: action === 'accept' ? 'Xác nhận chấp nhận' : 'Xác nhận từ chối',
      exact: true,
    })
    .click();
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`proposal-${name}.png`);
  await page.screenshot({ path, fullPage: true });
  await info.attach(`proposal-${name}`, { path, contentType: 'image/png' });
}

test.setTimeout(90_000);
test.beforeEach(() => harness('reset'));

test('staff creates and replaces proposals, then student accepts persisted changes with stored duration', async ({
  page,
  browser,
}, info) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  let state = await submitProposal(page, fixture);
  expect(state).toMatchObject({
    appointmentStatus: 'CHANGE_PENDING',
    location: fixture.location,
    proposalVersion: 1,
  });
  expect(state.proposals[0].location).toBe(fixture.location);
  expect(Date.parse(state.proposals[0].endsAt) - Date.parse(state.proposals[0].startsAt)).toBe(
    30 * 60_000,
  );
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Đề xuất đổi lịch', exact: true })).toBeVisible();
  state = await submitProposal(page, fixture, '12:00', 'clear');
  expect(state.proposals.map((p) => p.status)).toEqual(['SUPERSEDED', 'PENDING']);
  expect(state.proposals[1].location).toBeNull();
  await screenshot(page, info, 'staff-replaced');

  const studentContext = await browser.newContext({
    baseURL: origin,
    timezoneId: 'America/Los_Angeles',
    viewport: page.viewportSize()!,
  });
  try {
    const student = await studentContext.newPage();
    await login(student, 'student');
    await student.goto(`/appointments/${fixture.id}`);
    await expect(
      student.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
    ).toBeVisible();
    await student.reload();
    await expect(
      student.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
    ).toBeVisible();
    await screenshot(student, info, 'student-pending');
    const decisions: { path: string; body: string | null }[] = [];
    student.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/accept'))
        decisions.push({ path: new URL(request.url()).pathname, body: request.postData() });
    });
    harness('arm-loss');
    await decide(student, 'accept');
    await expect.poll(() => inspect(fixture.id).appointmentStatus).toBe('UPCOMING');
    await expect(
      student.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }),
    ).toBeVisible();
    const committed = inspect(fixture.id);
    await student.reload();
    await expect(
      student.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }),
    ).toBeVisible();
    await student.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }).click();
    await expect(student.getByLabel('Phục hồi thao tác đổi lịch')).toHaveCount(0);
    expect(inspect(fixture.id)).toEqual(committed);
    expect(decisions).toHaveLength(2);
    expect(decisions[1]).toEqual(decisions[0]);
    expect(decisions[0].body).toBe('{}');
    await expect(
      student.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
    ).toHaveCount(0);
    await expect(student.getByText('12:00', { exact: false }).first()).toBeVisible();
    await screenshot(student, info, 'student-accepted');
    state = inspect(fixture.id);
    expect(state.proposals.map((p) => p.status)).toEqual(['SUPERSEDED', 'ACCEPTED']);
    expect(state.location).toBeNull();
    expect(state.durationMinutes).toBe(30);
    expect(Date.parse(state.endsAt) - Date.parse(state.startsAt)).toBe(30 * 60_000);
    expect(state.audits).toMatchObject({
      CHANGE_PROPOSAL_CREATED: 1,
      CHANGE_PROPOSAL_REPLACED: 1,
      CHANGE_PROPOSAL_ACCEPTED: 1,
    });
    expect(state.notifications).toMatchObject({
      CHANGE_PROPOSAL_CREATED: 2,
      CHANGE_PROPOSAL_REPLACED: 2,
      CHANGE_PROPOSAL_ACCEPTED: 2,
    });
    await page.reload();
    await expect(page.getByText('12:00', { exact: false }).first()).toBeVisible();
    expect(inspect(fixture.id)).toEqual(state);
  } finally {
    await studentContext.close();
  }
});

test('declining a proposal explicitly cancels the appointment and survives reload without duplicate effects', async ({
  page,
  browser,
}, info) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  await submitProposal(page, fixture, '11:30', 'Phòng kiểm thử B305');
  const studentContext = await browser.newContext({
    baseURL: origin,
    timezoneId: 'America/Los_Angeles',
    viewport: page.viewportSize()!,
  });
  try {
    const student = await studentContext.newPage();
    await login(student, 'student');
    await student.goto(`/appointments/${fixture.id}`);
    await student.getByRole('button', { name: 'Từ chối đề xuất', exact: true }).click();
    const confirmation = student.getByRole('dialog');
    await expect(confirmation).toContainText(/hủy/i);
    await screenshot(student, info, 'decline-confirmation');
    const decisions: { path: string; body: string | null }[] = [];
    student.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/decline'))
        decisions.push({ path: new URL(request.url()).pathname, body: request.postData() });
    });
    harness('arm-loss');
    await student.getByRole('button', { name: 'Xác nhận từ chối', exact: true }).click();
    await expect.poll(() => inspect(fixture.id).appointmentStatus).toBe('CANCELLED');
    await expect(
      student.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }),
    ).toBeVisible();
    const committed = inspect(fixture.id);
    await student.reload();
    await expect(
      student.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }),
    ).toBeVisible();
    await student.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }).click();
    await expect(student.getByLabel('Phục hồi thao tác đổi lịch')).toHaveCount(0);
    await expect(student.getByText('Đã hủy', { exact: true }).first()).toBeVisible();
    await expect(
      student.getByText(/Lý do hủy: Sinh viên đã từ chối đề xuất đổi lịch/),
    ).toBeVisible();
    const state = inspect(fixture.id);
    expect(state).toEqual(committed);
    expect(decisions).toHaveLength(2);
    expect(decisions[1]).toEqual(decisions[0]);
    expect(decisions[0].body).toBe('{}');
    expect(state.proposals.map((p) => p.status)).toEqual(['DECLINED']);
    expect(state.audits.CHANGE_PROPOSAL_DECLINED).toBe(1);
    expect(state.notifications.CHANGE_PROPOSAL_DECLINED).toBe(2);
    await student.reload();
    expect(inspect(fixture.id)).toEqual(state);
  } finally {
    await studentContext.close();
  }
});

test('unknown create survives reload and replay returns its old receipt without replacing a newer proposal', async ({
  page,
  browser,
}) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  const dispatched: { body: string | null; key: string | undefined }[] = [];
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      request.url().endsWith(`/appointments/${fixture.id}/change-proposals`)
    )
      dispatched.push({ body: request.postData(), key: request.headers()['idempotency-key'] });
  });
  await reviewProposal(page, fixture, '11:00', 'Phòng mất phản hồi A');
  harness('arm-loss');
  await page.getByRole('button', { name: 'Xác nhận gửi đề xuất', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }),
  ).toBeVisible();
  const committed = inspect(fixture.id);
  expect(committed.proposals).toHaveLength(1);
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }),
  ).toBeVisible();
  const secondContext = await browser.newContext({ baseURL: origin });
  try {
    const second = await secondContext.newPage();
    await login(second, 'staff');
    const replacement = await createViaApi(second, fixture, 'Phòng thay thế sau mất phản hồi');
    const beforeRetry = inspect(fixture.id);
    await page.getByRole('button', { name: 'Thử lại thao tác đã gửi', exact: true }).click();
    await expect(page.getByLabel('Phục hồi thao tác đổi lịch')).toHaveCount(0);
    await expect(
      page.getByText('Phòng thay thế sau mất phản hồi', { exact: true }).first(),
    ).toBeVisible();
    expect(inspect(fixture.id)).toEqual(beforeRetry);
    expect(inspect(fixture.id).proposals[1]).toMatchObject({
      id: replacement.id,
      status: 'PENDING',
    });
    expect(dispatched).toHaveLength(2);
    expect(dispatched[1]).toEqual(dispatched[0]);
    expect(dispatched[0].key).toMatch(/^[a-f0-9-]{36}$/);
  } finally {
    await secondContext.close();
  }
});

test('two browsers reject a stale confirmed intention instead of silently replacing a proposal', async ({
  page,
  browser,
}) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  await reviewProposal(page, fixture, '11:00', 'Ý định từ tab cũ');
  const secondContext = await browser.newContext({ baseURL: origin });
  try {
    const second = await secondContext.newPage();
    await login(second, 'staff');
    await createViaApi(second, fixture, 'Đề xuất từ tab mới');
    const response = page.waitForResponse(
      (r) => r.status() === 409 && r.url().endsWith('/change-proposals'),
    );
    await page.getByRole('button', { name: 'Xác nhận gửi đề xuất', exact: true }).click();
    expect((await (await response).json()).error.code).toBe('PROPOSAL_PRECONDITION_FAILED');
    await expect(
      page.getByRole('alert').filter({ hasText: /thay đổi|mới|cập nhật/i }),
    ).toBeVisible();
    const state = inspect(fixture.id);
    expect(state.proposals).toHaveLength(1);
    expect(state.proposalVersion).toBe(1);
    expect(state.proposals[0].location).toBe('Đề xuất từ tab mới');
    expect(state.audits.CHANGE_PROPOSAL_CREATED).toBe(1);
    expect(state.audits.CHANGE_PROPOSAL_REPLACED).toBe(0);
  } finally {
    await secondContext.close();
  }
});

test('acceptance failure keeps the server pending proposal and original reservation', async ({
  page,
  browser,
}) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  const pending = await submitProposal(page, fixture);
  harness('block-proposed-slot', '--id', pending.proposals[0].id);
  const studentContext = await browser.newContext({ baseURL: origin });
  try {
    const student = await studentContext.newPage();
    await login(student, 'student');
    await student.goto(`/appointments/${fixture.id}`);
    const response = student.waitForResponse(
      (r) => r.url().endsWith('/accept') && r.status() === 409,
    );
    await decide(student, 'accept');
    expect((await (await response).json()).error.code).toBe('STAFF_UNAVAILABLE');
    await expect(
      student.getByRole('alert').filter({ hasText: /khả dụng|trống|bận/i }),
    ).toBeVisible();
    await student.reload();
    await expect(
      student.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
    ).toBeVisible();
    expect(inspect(fixture.id)).toEqual(pending);
  } finally {
    await studentContext.close();
  }
});

test('worker restart consumes persisted overdue proposal while an open student page refreshes', async ({
  page,
  browser,
}, info) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  const pending = await submitProposal(page, fixture);
  const proposalId = pending.proposals[0].id;
  harness('worker-once');
  expect(inspect(fixture.id)).toEqual(pending);
  const studentContext = await browser.newContext({
    baseURL: origin,
    viewport: page.viewportSize()!,
  });
  try {
    const student = await studentContext.newPage();
    await login(student, 'student');
    await student.goto(`/appointments/${fixture.id}`);
    await expect(
      student.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
    ).toBeVisible();
    harness('make-proposal-due', '--id', proposalId);
    expect(inspect(fixture.id).appointmentStatus).toBe('CHANGE_PENDING');
    harness('worker-once');
    await expect(
      student.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
    ).toHaveCount(0, { timeout: 20_000 });
    await expect(student.getByText('Đã hủy', { exact: true }).first()).toBeVisible();
    await expect(
      student.getByText(/Lý do hủy: Đề xuất đổi lịch đã hết hạn phản hồi/),
    ).toBeVisible();
    await screenshot(student, info, 'worker-expired');
    const expired = inspect(fixture.id);
    expect(expired).toMatchObject({ appointmentStatus: 'CANCELLED', proposalVersion: 1 });
    expect(expired.proposals.map((p) => p.status)).toEqual(['EXPIRED']);
    expect(expired.audits.CHANGE_PROPOSAL_EXPIRED).toBe(1);
    expect(expired.notifications.CHANGE_PROPOSAL_EXPIRED).toBe(2);
    harness('worker-once');
    expect(inspect(fixture.id)).toEqual(expired);
    await student.reload();
    await expect(student.getByText('Đã hủy', { exact: true }).first()).toBeVisible();
  } finally {
    await studentContext.close();
  }
});

test('proposal history and mutations reject unrelated participants and unsupported roles', async ({
  page,
  browser,
}) => {
  const fixture = seed();
  await login(page, 'staff');
  await openStaff(page, fixture);
  const pending = await submitProposal(page, fixture);
  for (const account of ['other', 'otherstaff', 'admin'] as const) {
    const isolated = await browser.newContext({ baseURL: origin });
    try {
      const outsider = await isolated.newPage();
      await login(outsider, account);
      const studentPath = `/api/v1/appointments/${fixture.id}/change-proposals`;
      const staffPath = `/api/v1/staff/me/appointments/${fixture.id}/change-proposals`;
      const expected = account === 'admin' ? 403 : 404;
      const history = await outsider.request.get(
        account === 'otherstaff' ? staffPath : studentPath,
      );
      expect(history.status()).toBe(expected);
      const csrf = await outsider.request.get('/api/v1/auth/csrf');
      const { csrf_token: token } = (await csrf.json()) as { csrf_token: string };
      const decision = await outsider.request.post(
        `${studentPath}/${pending.proposals[0].id}/accept`,
        {
          headers: { Origin: origin, 'X-CSRF-Token': token },
          data: {},
        },
      );
      expect(decision.status()).toBe(account === 'other' ? 404 : 403);
      await outsider.goto(
        account === 'otherstaff'
          ? `/staff/appointments/${fixture.id}`
          : `/appointments/${fixture.id}`,
      );
      await expect(
        outsider.getByRole('button', { name: 'Chấp nhận đề xuất', exact: true }),
      ).toHaveCount(0);
      await expect(outsider.getByText(fixture.location, { exact: true })).toHaveCount(0);
      expect(inspect(fixture.id)).toEqual(pending);
    } finally {
      await isolated.close();
    }
  }
});
