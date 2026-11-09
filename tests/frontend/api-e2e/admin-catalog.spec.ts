import { expect, test } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  apiWrite,
  confirmed,
  editPurpose,
  fixture,
  harness,
  inspectCatalog,
  login,
  password,
  purposeReplace,
  readPurpose,
  root,
  savePurpose,
  secondAdmin,
} from './admin-configuration-helpers';

test.beforeEach(() => harness('reset'));

test('admin catalog persists create/edit/deactivation and retains immutable codes', async ({
  page,
}) => {
  await login(page);
  await page.goto('/admin/purposes');
  await page.getByRole('button', { name: 'Tạo nhu cầu', exact: true }).click();
  await page.getByLabel('Mã nhu cầu').fill('CAREER_E2E');
  await page.getByLabel('Tên nhu cầu').fill('Tư vấn mới');
  await page.getByLabel('Thời lượng (phút)').fill('17');
  await savePurpose(page);
  await confirmed(page);
  await page.reload();
  await page.getByRole('button', { name: 'Sửa CAREER_E2E', exact: true }).click();
  await expect(page.getByLabel('Mã nhu cầu')).toHaveAttribute('readonly', '');
  await page.getByLabel('Hoạt động', { exact: true }).uncheck();
  await savePurpose(page);
  await confirmed(page);
  await page.reload();
  await page.getByLabel('Lọc trạng thái nhu cầu').selectOption('inactive');
  await expect(page.getByRole('button', { name: 'Sửa CAREER_E2E' })).toBeVisible();
  const state = inspectCatalog();
  expect(state.purpose_count).toBe(2);
  expect(state.audit_actions).toEqual(['PURPOSE_CREATED', 'PURPOSE_UPDATED']);
  expect(state.metadata_safe).toBe(true);
});

test('matching POST replays and stale matching PUTs create no extra audit effects', async ({
  page,
}) => {
  await login(page);
  const before = await readPurpose(page);
  const create = {
    code: 'REPLAY_E2E',
    name: 'Replay fixture',
    duration_minutes: 30,
    requires_free_text: false,
    is_active: true,
  };
  const first = await apiWrite(page, 'POST', '/admin/purposes', create);
  expect(first.status()).toBe(201);
  const id = (await first.json()).id;
  const repeat = await apiWrite(page, 'POST', '/admin/purposes', create);
  expect(repeat.status()).toBe(201);
  expect((await repeat.json()).id).toBe(id);
  const duplicate = await apiWrite(page, 'POST', '/admin/purposes', {
    ...create,
    duration_minutes: 31,
  });
  expect(duplicate.status()).toBe(409);
  const noop = await apiWrite(page, 'PUT', `/admin/purposes/${before.id}`, {
    ...purposeReplace(before),
    expected_updated_at: '2020-01-01T00:00:00.000001Z',
  });
  expect(noop.status()).toBe(200);
  expect((await noop.json()).updated_at).toBe(before.updated_at);
  expect(inspectCatalog().audit_actions).toEqual(['PURPOSE_CREATED']);
});

test('a referenced purpose deactivates without deleting staff links or existing appointment history', async ({
  page,
  context,
}) => {
  // Seed an existing referenced appointment using the existing owned fixture helper.
  const appointmentId = JSON.parse(harness('seed-timezone-appointment')).id as string;
  await login(page);
  const before = await readPurpose(page);
  await editPurpose(page);
  await page.getByLabel('Hoạt động', { exact: true }).uncheck();
  await savePurpose(page);
  await confirmed(page);
  expect((await readPurpose(page)).id).toBe(before.id);
  expect(inspectCatalog().staff_links).toBe(2);
  const student = await context.browser()!.newContext({ baseURL: new URL(page.url()).origin });
  try {
    const tab = await student.newPage();
    await login(tab, 'student');
    await tab.goto('/booking');
    await expect(tab.getByRole('radio', { name: /Tư vấn học tập kiểm thử/ })).toHaveCount(0);
    const detail = await tab.request.get(`/api/v1/appointments/${appointmentId}`);
    expect(detail.status()).toBe(200);
    expect((await detail.json()).purpose_id).toBe(before.id);
    await page.getByLabel('Hoạt động', { exact: true }).check();
    await savePurpose(page);
    await confirmed(page);
    await tab.reload();
    await expect(tab.getByRole('radio', { name: /Tư vấn học tập kiểm thử/ })).toBeVisible();
  } finally {
    await student.close();
  }
});

test('two ADMIN editors cannot overwrite the winning purpose update', async ({ page, context }) => {
  harness('seed-admin');
  await login(page);
  await editPurpose(page);
  const other = await secondAdmin(page, context);
  try {
    await editPurpose(other.tab);
    await page.getByLabel('Tên nhu cầu').fill('Winner');
    await savePurpose(page);
    await confirmed(page);
    await other.tab.getByLabel('Tên nhu cầu').fill('Loser');
    await savePurpose(other.tab);
    await expect(other.tab.getByRole('heading', { name: 'Dữ liệu đã thay đổi' })).toBeVisible();
    expect((await readPurpose(page)).name).toBe('Winner');
    await other.tab.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }).click();
    await other.tab
      .getByRole('button', { name: 'Tôi đã xem kết quả, bắt đầu chỉnh sửa mới' })
      .click();
    await expect(other.tab.getByLabel('Tên nhu cầu')).toHaveValue('Winner');
    expect(inspectCatalog().audit_actions).toEqual(['PURPOSE_UPDATED']);
  } finally {
    await other.browser.close();
  }
});

for (const kind of ['create', 'replace'] as const)
  test(`lost committed ${kind} receipt supports exact replay without duplicate effects`, async ({
    page,
  }) => {
    await login(page);
    await page.goto('/admin/purposes');
    if (kind === 'create') {
      await page.getByRole('button', { name: 'Tạo nhu cầu', exact: true }).click();
      await page.getByLabel('Mã nhu cầu').fill('LOST_E2E');
      await page.getByLabel('Tên nhu cầu').fill('Lost receipt');
    } else {
      await editPurpose(page);
      await page.getByLabel('Thời lượng (phút)').fill('17');
    }
    const bodies: string[] = [];
    page.on('request', (request) => {
      if (
        ['POST', 'PUT'].includes(request.method()) &&
        new URL(request.url()).pathname.startsWith('/api/v1/admin/purposes')
      )
        bodies.push(request.postData()!);
    });
    harness('arm-loss');
    await savePurpose(page);
    await expect(page.getByRole('heading', { name: 'Chưa xác định kết quả lưu' })).toBeVisible();
    await page.getByRole('button', { name: 'Gửi lại đúng yêu cầu cũ' }).click();
    await confirmed(page);
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    expect(inspectCatalog().audit_actions).toEqual([
      kind === 'create' ? 'PURPOSE_CREATED' : 'PURPOSE_UPDATED',
    ]);
  });

test('replacement sessions of the same ADMIN clear pending private UI and ignore a late receipt', async ({
  page,
  context,
}) => {
  await login(page);
  await editPurpose(page);
  await page.getByLabel('Tên nhu cầu').fill('Committed before session replacement');
  harness('arm-hold');
  try {
    await savePurpose(page);
    await expect
      .poll(() => existsSync(resolve(root, 'build/frontend-api/held-response')))
      .toBe(true);
    const tab = await context.newPage();
    await tab.goto('/admin/accounts');
    await tab.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await expect(tab).toHaveURL(/\/login/);
    await login(tab);
    await page.bringToFront();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page.getByRole('form', { name: 'Thông tin nhu cầu tư vấn' })).toHaveCount(0);
    harness('release-response');
    await expect(page.getByText('Backend đã xác nhận trạng thái yêu cầu.')).toHaveCount(0);
    expect(inspectCatalog().audit_actions).toEqual(['PURPOSE_UPDATED']);
    await tab.close();
  } finally {
    harness('release-response');
  }
});

for (const role of ['student', 'staff'] as const)
  test(`all new admin routes and API reads/writes deny ${role}`, async ({ page }) => {
    await login(page, role);
    for (const route of ['/admin/purposes', '/admin/service-hours', '/admin/booking-settings']) {
      await page.goto(route);
      await expect(page.getByRole('heading', { name: 'Không có quyền quản trị' })).toBeVisible();
    }
    for (const path of [
      '/admin/purposes',
      `/admin/purposes/${fixture().purpose}`,
      '/admin/booking-configuration',
    ])
      expect((await page.request.get(`/api/v1${path}`)).status()).toBe(403);
    expect((await apiWrite(page, 'PUT', '/admin/booking-configuration', {})).status()).toBe(403);
    expect((await apiWrite(page, 'POST', '/admin/purposes', {})).status()).toBe(403);
  });

test('anonymous deep link returns to the exact admin catalog after real authentication', async ({
  page,
}) => {
  await page.goto('/admin/purposes');
  await expect(page).toHaveURL(/\/login\?returnTo=%2Fadmin%2Fpurposes/);
  expect((await page.request.get('/api/v1/admin/purposes')).status()).toBe(401);
  await page.getByLabel('Email', { exact: true }).fill('admin@frontend.example.invalid');
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page).toHaveURL('/admin/purposes');
  await expect(page.getByRole('heading', { name: 'Danh mục tư vấn', exact: true })).toBeVisible();
});

test('invalid CSRF is rejected by the real backend without changing catalog state', async ({
  page,
}) => {
  await login(page);
  await editPurpose(page);
  await page.getByLabel('Tên nhu cầu').fill('Must not persist');
  await page.route('**/api/v1/admin/purposes/*', (route) =>
    route.request().method() === 'PUT'
      ? route.continue({ headers: { ...route.request().headers(), 'x-csrf-token': 'invalid' } })
      : route.continue(),
  );
  const rejected = page.waitForResponse(
    (r) => r.request().method() === 'PUT' && r.status() === 403,
  );
  await savePurpose(page);
  expect((await (await rejected).json()).error.code).toBe('INVALID_CSRF');
  expect((await readPurpose(page)).name).toBe('Tư vấn học tập kiểm thử');
  expect(inspectCatalog().audit_actions).toHaveLength(0);
});
