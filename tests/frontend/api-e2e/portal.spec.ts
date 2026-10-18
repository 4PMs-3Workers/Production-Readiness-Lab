import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname, '../../..');
const python = process.env.FRONTEND_E2E_PYTHON ?? 'python';
const password = process.env.FRONTEND_E2E_PASSWORD ?? 'Fictional-e2e-password!42';
const description = 'Nội dung giả lập cho bộ kiểm thử tự động.';
const harness = (action: string, ...args: string[]) =>
  execFileSync(python, ['tools/scripts/frontend_e2e_backend.py', action, ...args], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: 'src/backend' },
    encoding: 'utf8',
  });
async function login(page: Page, account = 'student') {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  if (account === 'student' || account === 'other')
    await expect(page.getByRole('heading', { name: /Chào mừng,/ })).toBeVisible();
}

async function selectBooking(page: Page) {
  await page.goto('/booking');
  await page.getByRole('radio', { name: /Tư vấn học tập kiểm thử/ }).check();
  await page.getByLabel('Bạn muốn trao đổi về điều gì? *').fill(description);
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /Cố vấn kiểm thử/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  const dates = page.getByRole('radio', { name: /^\d{2}\/\d{2}\/\d{4}$/ });
  await expect(dates).toHaveCount(7);
  await expect(page.getByRole('button', { name: 'Tuần sau', exact: true })).toHaveCount(0);
  await dates.first().check();
  await page.getByRole('radio', { name: /08:00 – 08:30/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
}

async function book(page: Page) {
  await selectBooking(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
  const id = new URL(page.url()).searchParams.get('id');
  expect(id).toMatch(/^[a-f0-9-]{36}$/);
  return id!;
}

test.beforeEach(() => {
  harness('reset');
});

test('session loss while the first committed receipt is pending preserves its original intent', async ({
  page,
  context,
}) => {
  await login(page);
  await selectBooking(page);
  const posted: { key: string | undefined; body: string | null }[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/appointments')
      posted.push({ key: request.headers()['idempotency-key'], body: request.postData() });
  });
  harness('arm-hold');
  try {
    await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
    await expect
      .poll(() => existsSync(resolve(root, 'build/frontend-api/held-response')))
      .toBe(true);
    const history = await context.request.get('/api/v1/appointments');
    expect(history.status()).toBe(200);
    const committed = (await history.json()).items;
    expect(committed).toHaveLength(1);
    const id = committed[0].id as string;
    expect(JSON.parse(harness('inspect', '--id', id))).toMatchObject({
      appointments: 1,
      APPOINTMENT_CREATED: 1,
      APPOINTMENT_CREATED_notifications: 2,
    });
    harness('expire', '--user', 'student');
    // The first response is still pending; no earlier unknown outcome is required.
    await expect(page.getByText('Đang xác nhận…', { exact: true })).toBeVisible();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page).toHaveURL(/\/login\?returnTo=%2Fbooking/);
    harness('release-response');
    await expect(page.getByText(description, { exact: true })).toHaveCount(0);
    await page.getByLabel('Email', { exact: true }).fill('student@frontend.example.invalid');
    await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await expect(page).toHaveURL('/booking');
    await page.getByRole('button', { name: 'Thử lại cùng yêu cầu' }).click();
    await expect(page.getByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
    expect(new URL(page.url()).searchParams.get('id')).toBe(id);
    expect(posted).toHaveLength(2);
    expect(posted[0].key).toBeTruthy();
    expect(posted[1]).toEqual(posted[0]);
    expect(JSON.parse(harness('inspect', '--id', id))).toMatchObject({
      appointments: 1,
      APPOINTMENT_CREATED: 1,
      APPOINTMENT_CREATED_notifications: 2,
    });
  } finally {
    harness('release-response');
  }
});

test('an authoritative conflict after an unknown POST unlocks another slot and a fresh intent', async ({
  page,
  context,
}) => {
  await login(page);
  await selectBooking(page);
  const posted: { key: string | undefined; body: string | null }[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/appointments')
      posted.push({ key: request.headers()['idempotency-key'], body: request.postData() });
  });
  // Abort before the backend sees the first POST; retain its actual key/body.
  await page.route('**/api/v1/appointments', (route) => route.abort('failed'), { times: 1 });
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  const retry = page.getByRole('button', { name: 'Thử lại cùng yêu cầu' });
  await expect(retry).toBeVisible();
  expect(posted).toHaveLength(1);
  expect((await (await context.request.get('/api/v1/appointments')).json()).items).toEqual([]);
  const csrf = await context.request.get('/api/v1/auth/csrf');
  const independent = await context.request.post('/api/v1/appointments', {
    headers: {
      Origin: 'http://127.0.0.1:3101',
      'X-CSRF-Token': (await csrf.json()).csrf_token as string,
      'Idempotency-Key': 'independent-winner',
    },
    data: JSON.parse(posted[0].body!),
  });
  expect(independent.status()).toBe(201);
  const id = (await independent.json()).id as string;
  const rejection = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/appointments' &&
      response.status() === 409,
  );
  await retry.click();
  expect((await (await rejection).json()).error.code).toBe('BOOKING_CONFLICT');
  const alternative = page.getByRole('radio', { name: /08:30 – 09:00/ });
  await expect(alternative).toBeVisible();
  await expect(alternative).not.toBeChecked();
  await expect(page.getByRole('radio', { name: /08:00 – 08:30/ })).toHaveCount(0);
  await expect(retry).toHaveCount(0);
  expect(posted[1]).toEqual(posted[0]);
  await alternative.check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
  const replacementId = new URL(page.url()).searchParams.get('id');
  expect(replacementId).not.toBe(id);
  expect(posted).toHaveLength(3);
  expect(posted[2].key).not.toBe(posted[0].key);
  expect(JSON.parse(posted[2].body!).starts_at).not.toBe(JSON.parse(posted[0].body!).starts_at);
  const finalHistory = (await (await context.request.get('/api/v1/appointments')).json()).items;
  expect(finalHistory).toHaveLength(2);
  for (const appointment of finalHistory)
    expect(JSON.parse(harness('inspect', '--id', appointment.id))).toMatchObject({
      appointments: 1,
      APPOINTMENT_CREATED: 1,
      APPOINTMENT_CREATED_notifications: 2,
    });
});

for (const heldPath of ['/auth/csrf', '/auth/me']) {
  test(`an account switch while awaiting ${heldPath} never posts the previous student's booking`, async ({
    page,
    context,
  }) => {
    await login(page);
    await selectBooking(page);
    let release!: () => void;
    let intercepted!: () => void;
    const held = new Promise<void>((resolve) => {
      intercepted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    // Hold timing only. All responses still come from the real FastAPI/MySQL backend.
    await page.route(`**/api/v1${heldPath}`, async (route) => {
      intercepted();
      await gate;
      await route.continue();
    });
    const posts: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/appointments')
        posts.push(request.url());
    });
    await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
    await held;
    const switched = await context.request.post('/api/v1/auth/login', {
      headers: { Origin: 'http://127.0.0.1:3101' },
      data: { email: 'other@frontend.example.invalid', password },
    });
    expect(switched.status()).toBe(200);
    release();
    await expect(page).toHaveURL(/\/login\?returnTo=/);
    expect(posts).toHaveLength(0);
    const otherHistory = await context.request.get('/api/v1/appointments');
    expect(otherHistory.status()).toBe(200);
    expect((await otherHistory.json()).items).toEqual([]);
    const restored = await context.request.post('/api/v1/auth/login', {
      headers: { Origin: 'http://127.0.0.1:3101' },
      data: { email: 'student@frontend.example.invalid', password },
    });
    expect(restored.status()).toBe(200);
    const originalHistory = await context.request.get('/api/v1/appointments');
    expect((await originalHistory.json()).items).toEqual([]);
  });
}

test('student profile has a sidebar route, survives reload and changes with the account', async ({
  page,
}) => {
  await page.goto('/profile');
  await expect(page).toHaveURL(/\/login\?returnTo=%2Fprofile/);
  await login(page);
  const navigation = page.getByRole('navigation', { name: 'Điều hướng chính' });
  await navigation.getByRole('link', { name: 'Thông tin sinh viên' }).click();
  await expect(page).toHaveURL('/profile');
  await expect(page.getByRole('heading', { name: 'Thông tin sinh viên', level: 1 })).toBeVisible();
  await expect(navigation.getByRole('link', { name: 'Thông tin sinh viên' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(page.getByText('TEST-STUDENT', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Khoa kiểm thử', { exact: true })).toBeVisible();
  await expect(page.getByText('Ngành kiểm thử', { exact: true })).toBeVisible();
  await expect(page.getByText('Không cung cấp', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('TEST-STUDENT', { exact: true }).first()).toBeVisible();
  const response = await page.request.get('/api/v1/students/me');
  expect(response.headers()['cache-control']).toBe('private, no-store');
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await login(page, 'other');
  await page
    .getByRole('navigation', { name: 'Điều hướng chính' })
    .getByRole('link', { name: 'Thông tin sinh viên' })
    .click();
  await expect(page.getByText('TEST-OTHER', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('TEST-STUDENT', { exact: true })).toHaveCount(0);
});

test('real login → booking → reload → history/detail → cancel → reload persists in MySQL', async ({
  page,
  context,
}, testInfo) => {
  await page.goto('/appointments');
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await login(page);
  expect((await context.cookies()).find((cookie) => cookie.name === 'prl_session')?.httpOnly).toBe(
    true,
  );
  const id = await book(page);
  await expect(page.getByText('Sắp diễn ra', { exact: true })).toBeVisible();
  await expect(page.getByText('08:00 – 08:30', { exact: true })).toBeVisible();
  await expect(page.getByText('DEMO', { exact: true })).toHaveCount(0);
  const screenshot = testInfo.outputPath('real-result.png');
  await page.screenshot({
    path: screenshot,
    fullPage: true,
    mask: [page.getByText(description, { exact: true })],
  });
  await testInfo.attach('real-result-redacted', { path: screenshot, contentType: 'image/png' });
  await page.reload();
  await expect(page.getByText('Sắp diễn ra', { exact: true })).toBeVisible();
  await page.goto('/appointments');
  await page.getByRole('link', { name: /Xem chi tiết/ }).click();
  await expect(page).toHaveURL(new RegExp(`/appointments/${id}$`));
  await page.getByRole('button', { name: 'Hủy lịch hẹn', exact: true }).click();
  await page.getByLabel('Lý do hủy (không bắt buộc)').fill('Fictional test cancellation');
  await page.getByRole('button', { name: 'Xác nhận hủy lịch', exact: true }).click();
  await expect(page.getByText('Đã hủy', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Đã hủy', { exact: true })).toBeVisible();
  const csrf = await context.request.get('/api/v1/auth/csrf');
  const token = (await csrf.json()).csrf_token as string;
  const repeated = await context.request.post(`/api/v1/appointments/${id}/cancel`, {
    headers: { Origin: 'http://127.0.0.1:3101', 'X-CSRF-Token': token },
    data: {},
  });
  expect(repeated.status()).toBe(200);
  const counts = JSON.parse(harness('inspect', '--id', id));
  expect(counts).toEqual({
    appointments: 1,
    APPOINTMENT_CREATED: 1,
    APPOINTMENT_CANCELLED: 1,
    APPOINTMENT_CREATED_notifications: 2,
    APPOINTMENT_CANCELLED_notifications: 2,
  });
});

test('a lost committed response survives route remount and retries exactly once', async ({
  page,
}) => {
  await login(page);
  await selectBooking(page);
  harness('arm-loss');
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('button', { name: 'Thử lại cùng yêu cầu' })).toBeVisible();
  // Client navigation preserves the UI-session intent; no page reload or new POST.
  await page.getByRole('link', { name: 'Trang chủ', exact: true }).first().click();
  await page.getByRole('link', { name: 'Bắt đầu đặt lịch' }).click();
  await page.getByRole('button', { name: 'Thử lại cùng yêu cầu' }).click();
  await expect(page.getByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
  const id = new URL(page.url()).searchParams.get('id')!;
  expect(JSON.parse(harness('inspect', '--id', id))).toMatchObject({
    appointments: 1,
    APPOINTMENT_CREATED: 1,
    APPOINTMENT_CREATED_notifications: 2,
  });
  await page.goto('/appointments');
  await expect(page.getByRole('link', { name: /Xem chi tiết/ })).toHaveCount(1);
});

test('two independent students cannot read or cancel each other’s appointments', async ({
  page,
  browser,
}) => {
  await login(page);
  const id = await book(page);
  const second = await browser.newContext({
    baseURL: 'http://127.0.0.1:3101',
    timezoneId: 'America/Los_Angeles',
  });
  try {
    const other = await second.newPage();
    await login(other, 'other');
    await other.goto(`/appointments/${id}`);
    await expect(other.getByRole('heading', { name: 'Không tìm thấy lịch hẹn.' })).toBeVisible();
    const csrf = await second.request.get('/api/v1/auth/csrf');
    const token = (await csrf.json()).csrf_token as string;
    const response = await second.request.post(`/api/v1/appointments/${id}/cancel`, {
      headers: { Origin: 'http://127.0.0.1:3101', 'X-CSRF-Token': token },
      data: {},
    });
    expect(response.status()).toBe(404);
    expect(JSON.parse(harness('inspect', '--id', id)).APPOINTMENT_CANCELLED).toBe(0);
  } finally {
    await second.close();
  }
});

test('expiry and logout clear private history before another account signs in', async ({
  page,
}) => {
  await login(page);
  const id = await book(page);
  harness('expire');
  await page.reload();
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await login(page, 'other');
  await page.goto('/appointments');
  await expect(page.getByRole('heading', { name: 'Chưa có lịch hẹn phù hợp.' })).toBeVisible();
  await expect(page.getByText(id, { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Đăng nhập', exact: true })).toBeVisible();
});

for (const role of ['staff', 'admin']) {
  test(`unsupported ${role} never receives student controls`, async ({ page }) => {
    await login(page, role);
    await expect(page.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Xác nhận đặt lịch' })).toHaveCount(0);
  });
}

test('real pagination, all-state filters, empty result and deep links', async ({ page }) => {
  harness('history');
  await login(page);
  await page.goto('/appointments');
  await expect(page.getByText('Trang 1 · 24 kết quả')).toBeVisible();
  await expect(page.getByRole('link', { name: /Xem chi tiết/ })).toHaveCount(20);
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.getByRole('link', { name: /Xem chi tiết/ })).toHaveCount(4);
  await page.getByLabel('Trạng thái lịch hẹn').selectOption('UPCOMING');
  await expect(page.getByRole('heading', { name: 'Chưa có lịch hẹn phù hợp.' })).toBeVisible();
  await page.getByLabel('Trạng thái lịch hẹn').selectOption('CANCELLED');
  await expect(page.getByRole('link', { name: /Xem chi tiết/ })).toHaveCount(20);
  await page
    .getByRole('link', { name: /Xem chi tiết/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/appointments\/[a-f0-9-]{36}$/);
  await page.reload();
  await expect(page.getByText('Đã hủy', { exact: true })).toBeVisible();
  await page.goto('/booking/result');
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toBeVisible();
});

for (const recovery of ['csrf-rejected', 'same-student', 'different-student'] as const) {
  test(`a lost committed receipt survives ${recovery} without a duplicate appointment`, async ({
    page,
    context,
  }) => {
    await login(page);
    await selectBooking(page);
    const posted: { key: string | undefined; body: string | null }[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/appointments')
        posted.push({ key: request.headers()['idempotency-key'], body: request.postData() });
    });
    harness('arm-loss');
    await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
    const retry = page.getByRole('button', { name: 'Thử lại cùng yêu cầu' });
    await expect(retry).toBeVisible();
    const committedHistory = await context.request.get('/api/v1/appointments');
    const committed = (await committedHistory.json()).items;
    expect(committed).toHaveLength(1);
    const id = committed[0].id as string;
    if (recovery === 'csrf-rejected') {
      // Corrupt one request header; let the real server return INVALID_CSRF.
      await page.route(
        '**/api/v1/appointments',
        async (route) => {
          await route.continue({
            headers: { ...route.request().headers(), 'x-csrf-token': 'invalid-test-token' },
          });
        },
        { times: 1 },
      );
      const rejection = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === '/api/v1/appointments' && response.status() === 403,
      );
      await retry.click();
      expect((await (await rejection).json()).error.code).toBe('INVALID_CSRF');
      await expect(retry).toBeVisible();
    } else {
      harness('expire', '--user', 'student');
      await retry.click();
      await expect(page).toHaveURL(/\/login\?returnTo=%2Fbooking/);
      await expect(page.getByText(description, { exact: true })).toHaveCount(0);
      const account = recovery === 'same-student' ? 'student' : 'other';
      await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
      await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
      await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
      await expect(page).toHaveURL('/booking');
      if (recovery === 'different-student') {
        await expect(page.getByRole('heading', { name: 'Chọn nhu cầu tư vấn' })).toBeVisible();
        await expect(retry).toHaveCount(0);
        expect(posted).toHaveLength(1);
        const otherHistory = await context.request.get('/api/v1/appointments');
        expect((await otherHistory.json()).items).toEqual([]);
      }
    }
    if (recovery !== 'different-student') {
      await retry.click();
      await expect(page.getByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
      expect(new URL(page.url()).searchParams.get('id')).toBe(id);
      expect(posted.length).toBe(recovery === 'csrf-rejected' ? 3 : 2);
      expect(posted[0].key).toBeTruthy();
      for (const replay of posted) expect(replay).toEqual(posted[0]);
    }
    expect(JSON.parse(harness('inspect', '--id', id))).toMatchObject({
      appointments: 1,
      APPOINTMENT_CREATED: 1,
      APPOINTMENT_CREATED_notifications: 2,
    });
  });
}
