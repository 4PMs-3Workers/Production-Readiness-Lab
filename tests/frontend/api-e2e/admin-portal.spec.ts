import { expect, test, type Page, type BrowserContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import type { AdminAccount } from '../../../src/frontend/services/admin-contracts';

const root = resolve(__dirname, '../../..');
const python = process.env.FRONTEND_E2E_PYTHON ?? 'python';
const password = process.env.FRONTEND_E2E_PASSWORD ?? 'Fictional-e2e-password!42';
const harness = (action: string, ...args: string[]) =>
  execFileSync(python, ['tools/scripts/frontend_e2e_backend.py', action, ...args], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: 'src/backend' },
    encoding: 'utf8',
  });
const inspect = (id: string) =>
  JSON.parse(harness('inspect-admin', '--id', id)) as {
    name: string;
    role: string;
    is_active: boolean;
    updated_at: string;
    audits: { action: string; actor: string; entity: string }[];
    revoked_sessions: number;
    active_sessions: number;
    metadata_safe: boolean;
  };
async function login(page: Page, name = 'admin', secret = password) {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(`${name}@frontend.example.invalid`);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(secret);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}
async function find(page: Page, email: string) {
  await page.getByLabel('Tìm theo tên hoặc email').fill(email);
  const read = page.waitForResponse(
    (r) =>
      r.request().method() === 'GET' &&
      r.url().includes('/admin/accounts?') &&
      r.url().includes('search='),
  );
  await page.getByRole('button', { name: 'Tìm kiếm', exact: true }).click();
  await read;
  await expect(page.getByRole('button', { name: `Sửa ${email}`, exact: true })).toBeVisible();
}
async function account(page: Page, email: string) {
  const response = await page.request.get('/api/v1/admin/accounts', { params: { search: email } });
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { items: AdminAccount[] };
  const found = body.items.find((row) => row.email === email);
  expect(found).toBeTruthy();
  return found!;
}
async function edit(page: Page, email = 'target@frontend.example.invalid') {
  await find(page, email);
  await page.getByRole('button', { name: `Sửa ${email}`, exact: true }).click();
}
async function update(page: Page, name = 'Tên đã cập nhật') {
  await page.getByRole('textbox', { name: 'Tên', exact: true }).fill(name);
  await page.getByRole('button', { name: 'Lưu thay đổi', exact: true }).click();
}
async function reset(page: Page, email: string, secret: string) {
  await find(page, email);
  await page.getByRole('button', { name: `Đặt lại mật khẩu ${email}`, exact: true }).click();
  await page.getByLabel('Mật khẩu mới').fill(secret);
  await page.getByRole('button', { name: 'Tiếp tục đặt lại mật khẩu' }).click();
  await page.getByRole('button', { name: 'Xác nhận thay đổi' }).click();
}
async function authenticatedContext(page: Page, context: BrowserContext, name: string) {
  const other = await context.browser()!.newContext({ baseURL: new URL(page.url()).origin });
  const tab = await other.newPage();
  await tab.goto(new URL('/login', page.url()).toString());
  await tab.getByLabel('Email', { exact: true }).fill(`${name}@frontend.example.invalid`);
  await tab.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await tab.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(tab).not.toHaveURL(/\/login/);
  return { other, tab };
}

test.beforeEach(() => {
  harness('reset');
});
test('real ADMIN shell, reload, server filtering, pagination and mobile keyboard entry', async ({
  page,
}) => {
  harness('seed-admin');
  await login(page);
  await expect(page.getByRole('heading', { name: 'Quản lý tài khoản' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Điều hướng quản trị' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.getByRole('button', { name: 'Trang trước' })).toBeEnabled();
  await page.getByLabel('Lọc vai trò').selectOption('ADMIN');
  await expect(page.getByRole('button', { name: 'Trang trước' })).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Sửa admin@frontend.example.invalid', exact: true }),
  ).toBeVisible();
  await page.getByLabel('Lọc vai trò').selectOption('');
  await page.getByLabel('Lọc trạng thái').selectOption('false');
  await expect(
    page.getByRole('button', { name: 'Sửa inactive@frontend.example.invalid', exact: true }),
  ).toBeVisible();
  await page.getByLabel('Tìm theo tên hoặc email').fill('absent@example.invalid');
  await page.getByLabel('Tìm theo tên hoặc email').press('Enter');
  await expect(page.getByText('Không có tài khoản phù hợp bộ lọc.')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
test('anonymous, STUDENT and STAFF never receive admin content or API access', async ({ page }) => {
  await page.goto('/admin/accounts');
  await expect(page).toHaveURL(/\/login/);
  for (const role of ['student', 'staff']) {
    await login(page, role);
    await page.goto('/admin/accounts');
    await expect(page.getByRole('heading', { name: 'Không có quyền quản trị' })).toBeVisible();
    expect((await page.request.get('/api/v1/admin/accounts')).status()).toBe(403);
    await expect(page.getByLabel('Tìm theo tên hoặc email')).toHaveCount(0);
    await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
  }
});
test('create and edit persist with exactly one audit; duplicate email and no-op do not mutate', async ({
  page,
}) => {
  await login(page);
  await page.getByRole('button', { name: 'Tạo tài khoản', exact: true }).click();
  await page.getByLabel('Email', { exact: true }).fill('NewAccount@frontend.example.invalid');
  await page.getByRole('textbox', { name: 'Tên', exact: true }).fill('Tài khoản mới');
  await page.getByLabel('Mật khẩu mới').fill(password);
  await page
    .getByRole('form', { name: 'Thông tin tài khoản' })
    .getByRole('button', { name: 'Tạo tài khoản', exact: true })
    .click();
  await expect(page.getByText('Đã tạo tài khoản.')).toBeVisible();
  await page.reload();
  const email = 'newaccount@frontend.example.invalid';
  await edit(page, email);
  const created = await account(page, email);
  expect(inspect(created.id).audits.map((a) => a.action)).toEqual(['ACCOUNT_CREATED']);
  await page.getByRole('button', { name: 'Lưu thay đổi' }).click();
  await expect(page.getByText('Không có thay đổi.')).toBeVisible();
  await update(page);
  await expect(page.getByText('Đã lưu tài khoản.')).toBeVisible();
  await page.reload();
  await edit(page, email);
  await expect(page.getByRole('textbox', { name: 'Tên', exact: true })).toHaveValue(
    'Tên đã cập nhật',
  );
  const checked = inspect(created.id);
  expect(checked.audits).toHaveLength(2);
  expect(checked.metadata_safe).toBe(true);
  await page.getByLabel('Email', { exact: true }).fill('ADMIN@frontend.example.invalid');
  await page.getByRole('button', { name: 'Lưu thay đổi' }).click();
  await expect(page.getByText(/Email đã được sử dụng/)).toBeVisible();
  expect(inspect(created.id).audits).toHaveLength(2);
  // API exact no-op must contain a real field and preserves microsecond precondition.
  const latest = await account(page, email);
  const csrf = await (await page.request.get('/api/v1/auth/csrf')).json();
  const noOp = await page.request.patch(`/api/v1/admin/accounts/${created.id}`, {
    headers: { 'X-CSRF-Token': csrf.csrf_token, Origin: 'http://127.0.0.1:3101' },
    data: { name: latest.name, expected_updated_at: latest.updated_at },
  });
  expect(noOp.status()).toBe(200);
  expect((await noOp.json()).updated_at).toBe(latest.updated_at);
  expect(inspect(created.id).audits).toHaveLength(2);
});
test('role/activation confirmation, persisted profile clearing and real target session revocation', async ({
  page,
  context,
}) => {
  harness('seed-admin');
  await login(page);
  const email = 'target@frontend.example.invalid';
  const target = await account(page, email);
  const { other, tab } = await authenticatedContext(page, context, 'target');
  try {
    await edit(page, email);
    await page.getByLabel('Vai trò', { exact: true }).selectOption('STAFF');
    await page.getByRole('button', { name: 'Lưu thay đổi' }).click();
    await expect(page.getByRole('button', { name: 'Quay lại chỉnh sửa' })).toBeFocused();
    await page.getByRole('button', { name: 'Quay lại chỉnh sửa' }).press('Enter');
    expect(inspect(target.id).audits).toHaveLength(0);
    await page.getByRole('button', { name: 'Lưu thay đổi' }).click();
    await page.getByRole('button', { name: 'Xác nhận thay đổi' }).click();
    await expect(page.getByText('Đã lưu tài khoản.')).toBeVisible();
    expect((await tab.request.get('http://127.0.0.1:3101/api/v1/auth/me')).status()).toBe(401);
    await page.reload();
    await edit(page, email);
    await expect(page.getByLabel('Vai trò', { exact: true })).toHaveValue('STAFF');
    await page.getByLabel('Trạng thái', { exact: true }).selectOption('false');
    await page.getByRole('button', { name: 'Lưu thay đổi' }).click();
    await page.getByRole('button', { name: 'Xác nhận thay đổi' }).click();
    await expect(page.getByText('Đã lưu tài khoản.')).toBeVisible();
    expect(inspect(target.id)).toMatchObject({
      role: 'STAFF',
      is_active: false,
      revoked_sessions: 1,
      student_code: null,
    });
  } finally {
    await other.close();
  }
});
for (const policy of [
  'last-admin',
  'last-staff',
  'occupied',
  'occupied-pending',
  'referenced',
] as const)
  test(`policy ${policy} rejects without a partial mutation or audit`, async ({ page }) => {
    const email =
      policy === 'last-admin' ? 'admin@frontend.example.invalid' : 'staff@frontend.example.invalid';
    if (policy === 'last-staff') harness('admin-last-staff');
    if (policy === 'occupied') harness('seed-staff-appointments');
    if (policy === 'occupied-pending') {
      const seeded = JSON.parse(harness('seed-proposal-appointment')) as {
        id: string;
        nextDate: string;
      };
      await login(page, 'staff');
      const path = `/api/v1/staff/me/appointments/${seeded.id}/change-proposals`;
      const { context: history } = await (await page.request.get(path)).json();
      const { csrf_token: token } = await (await page.request.get('/api/v1/auth/csrf')).json();
      const proposal = await page.request.post(path, {
        headers: {
          Origin: 'http://127.0.0.1:3101',
          'X-CSRF-Token': token,
          'Idempotency-Key': crypto.randomUUID(),
        },
        data: {
          expected_proposal_version: history.proposal_version,
          expected_pending_proposal_id: history.pending_proposal_id,
          proposed_starts_at: `${seeded.nextDate}T12:00:00+07:00`,
          proposed_location: null,
        },
      });
      expect(proposal.status()).toBe(201);
      await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
      await expect(page).toHaveURL(/\/login/);
    }
    await login(page);
    const target = await account(page, email);
    await edit(page, email);
    if (policy === 'referenced')
      await page.getByLabel('Vai trò', { exact: true }).selectOption('STUDENT');
    else await page.getByLabel('Trạng thái', { exact: true }).selectOption('false');
    await page.getByRole('button', { name: 'Lưu thay đổi' }).click();
    await page.getByRole('button', { name: 'Xác nhận thay đổi' }).click();
    const copy =
      policy === 'last-admin'
        ? /Phải còn ít nhất một tài khoản ADMIN/
        : policy === 'last-staff'
          ? /Phải còn ít nhất một tài khoản STAFF/
          : policy.startsWith('occupied')
            ? /Không thể ngừng hoạt động STAFF/
            : /Không thể đổi vai trò/;
    await expect(page.getByText(copy)).toBeVisible();
    expect(inspect(target.id)).toMatchObject({ is_active: true, role: target.role, audits: [] });
  });
test('two ADMIN contexts require conflict review before a new deliberate write', async ({
  page,
  context,
}) => {
  harness('seed-admin');
  await login(page);
  const { other, tab } = await authenticatedContext(page, context, 'admin2');
  try {
    const email = 'target@frontend.example.invalid';
    const target = await account(page, email);
    await edit(page, email);
    await edit(tab, email);
    await update(page, 'Bản A');
    await expect(page.getByText('Đã lưu tài khoản.')).toBeVisible();
    await update(tab, 'Bản B');
    await expect(tab.getByText(/Tài khoản đã được cập nhật ở phiên khác/)).toBeVisible();
    expect(inspect(target.id).name).toBe('Bản A');
    expect(inspect(target.id).audits).toHaveLength(1);
    await tab.getByRole('button', { name: 'Tải lại để xem xét' }).click();
    await tab.getByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' }).click();
    await edit(tab, email);
    await update(tab, 'Bản B đã xem xét');
    await expect(tab.getByText('Đã lưu tài khoản.')).toBeVisible();
    expect(inspect(target.id).name).toBe('Bản B đã xem xét');
    expect(inspect(target.id).audits).toHaveLength(2);
  } finally {
    await other.close();
  }
});
test('password reset revokes actual sessions and only the new password authorizes login', async ({
  page,
  context,
}) => {
  harness('seed-admin');
  await login(page);
  const email = 'target@frontend.example.invalid';
  const target = await account(page, email);
  const { other, tab } = await authenticatedContext(page, context, 'target');
  try {
    await reset(page, email, 'Changed-password!51');
    await expect(page.getByText(/Đã đặt lại mật khẩu/)).toBeVisible();
    expect((await tab.request.get('http://127.0.0.1:3101/api/v1/auth/me')).status()).toBe(401);
    await tab.goto('http://127.0.0.1:3101/login');
    await tab.getByLabel('Email', { exact: true }).fill(email);
    await tab.getByLabel('Mật khẩu', { exact: true }).fill(password);
    await tab.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await expect(tab.getByText('Email hoặc mật khẩu chưa đúng.')).toBeVisible();
    await tab.getByLabel('Mật khẩu', { exact: true }).fill('Changed-password!51');
    await tab.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await expect(tab).toHaveURL(/\/home$/);
    expect(inspect(target.id).audits.map((a) => a.action)).toEqual(['ACCOUNT_PASSWORD_RESET']);
    await page.reload();
    await expect(page.getByLabel('Mật khẩu mới')).toHaveCount(0);
  } finally {
    await other.close();
  }
});
test('self reset clears admin data and requires real reauthentication', async ({ page }) => {
  await login(page);
  await reset(page, 'admin@frontend.example.invalid', 'Changed-admin!51');
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole('list', { name: 'Danh sách tài khoản' })).toHaveCount(0);
  await login(page, 'admin', 'Changed-admin!51');
  await expect(page.getByRole('heading', { name: 'Quản lý tài khoản' })).toBeVisible();
});
for (const kind of ['create', 'update', 'password'] as const)
  test(`committed ${kind} with lost receipt stays unknown with exactly one audit`, async ({
    page,
  }) => {
    harness('seed-admin');
    await login(page);
    const email =
      kind === 'create' ? 'uncertain@frontend.example.invalid' : 'target@frontend.example.invalid';
    harness('arm-loss');
    if (kind === 'create') {
      await page.getByRole('button', { name: 'Tạo tài khoản', exact: true }).click();
      await page.getByLabel('Email', { exact: true }).fill(email);
      await page.getByRole('textbox', { name: 'Tên', exact: true }).fill('Kết quả chưa rõ');
      await page.getByLabel('Mật khẩu mới').fill(password);
      await page
        .getByRole('form', { name: 'Thông tin tài khoản' })
        .getByRole('button', { name: 'Tạo tài khoản', exact: true })
        .click();
    } else if (kind === 'password') await reset(page, email, 'Uncertain-password!51');
    else {
      await edit(page, email);
      await update(page, 'Kết quả chưa rõ');
    }
    await expect(page.getByText(/Chưa xác định kết quả/)).toBeVisible();
    const target = await account(page, email);
    expect(inspect(target.id).audits).toHaveLength(1);
    await page.getByRole('button', { name: 'Tải lại để xem xét' }).click();
    await expect(
      page.getByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' }),
    ).toBeVisible();
    expect(inspect(target.id).audits).toHaveLength(1);
    if (kind === 'password')
      await expect(page.getByText(/Mật khẩu không thể kiểm tra/)).toBeVisible();
  });
test('a held committed response cannot populate another identity after logout/login', async ({
  page,
}) => {
  harness('seed-admin');
  await login(page);
  await edit(page);
  harness('arm-hold');
  try {
    await update(page, 'Đã commit trước chuyển phiên');
    await expect
      .poll(() => existsSync(resolve(root, 'build/frontend-api/held-response')))
      .toBe(true);
    await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
    await login(page, 'admin2');
    harness('release-response');
    await expect(page.getByText('Đã lưu tài khoản.')).toHaveCount(0);
    await expect(page.getByRole('form', { name: 'Thông tin tài khoản' })).toHaveCount(0);
  } finally {
    harness('release-response');
  }
});
test('expired ADMIN session clears the editor and rejects requests until login', async ({
  page,
}) => {
  harness('seed-admin');
  await login(page);
  await edit(page);
  harness('expire', '--user', 'admin');
  await page.reload();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByLabel('Mật khẩu mới')).toHaveCount(0);
  expect((await page.request.get('/api/v1/admin/accounts')).status()).toBe(401);
});
