import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const root = resolve(__dirname, '../../..');
const python = process.env.FRONTEND_E2E_PYTHON ?? 'python';
const password = process.env.FRONTEND_E2E_PASSWORD ?? 'Fictional-e2e-password!42';
const harness = (action: string, ...args: string[]) =>
  execFileSync(python, ['tools/scripts/frontend_e2e_backend.py', action, ...args], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: 'src/backend' },
    encoding: 'utf8',
  });

async function login(page: Page, account: 'student' | 'staff' | 'other' | 'otherstaff' | 'admin') {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  if (account === 'student' || account === 'other')
    await expect(page.getByRole('heading', { name: /Chào mừng,/ })).toBeVisible();
  else if (account === 'staff' || account === 'otherstaff')
    await expect(page.getByRole('heading', { name: 'Lịch được giao' })).toBeVisible();
  else
    await expect(page.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
}

async function createBooking(page: Page) {
  await page.goto('/booking');
  await page.getByRole('radio', { name: /Tư vấn học tập kiểm thử/ }).check();
  await page
    .getByLabel('Bạn muốn trao đổi về điều gì? *')
    .fill('Nội dung riêng tư không xuất hiện trong thông báo.');
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /Cố vấn kiểm thử/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page
    .getByRole('radio', { name: /^\d{2}\/\d{2}\/\d{4}$/ })
    .first()
    .check();
  await page.getByRole('radio', { name: /08:00 – 08:30/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
  const id = new URL(page.url()).searchParams.get('id');
  expect(id).toMatch(/^[a-f0-9-]{36}$/);
  return id!;
}

test.beforeEach(() => harness('reset'));

test('booking notification is private to its participants and read state survives reload', async ({
  page,
}) => {
  await login(page, 'student');
  await createBooking(page);

  await page.goto('/notifications');
  const bookingNotification = page.getByText('Lịch hẹn đã được tạo').first();
  await expect(bookingNotification).toBeVisible();
  await expect(page.getByRole('link', { name: 'Xem lịch hẹn' }).first()).toHaveAttribute(
    'href',
    /\/appointments\/[a-f0-9-]+/,
  );
  await expect(page.getByText('Nội dung riêng tư không xuất hiện trong thông báo.')).toHaveCount(0);
  await page.getByRole('button', { name: 'Đánh dấu đã đọc' }).first().click();
  await expect(page.getByText('Đã đọc').first()).toBeVisible();
  await page.reload();
  await expect(page.getByText('Đã đọc').first()).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'staff');
  await page.goto('/staff/notifications');
  await expect(page.getByText('Lịch hẹn đã được tạo').first()).toBeVisible();
  await expect(page.getByRole('link', { name: 'Xem lịch hẹn' }).first()).toHaveAttribute(
    'href',
    /\/staff\/appointments\/[a-f0-9-]+/,
  );

  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'other');
  await page.goto('/notifications');
  await expect(page.getByText('Bạn chưa có thông báo nào.')).toBeVisible();
});

test('cancellation appears in unread inbox and expired session clears private state', async ({
  page,
}) => {
  await login(page, 'student');
  await createBooking(page);
  await page.goto('/appointments');
  await page.getByRole('link', { name: /Xem chi tiết/ }).click();
  await page.getByRole('button', { name: 'Hủy lịch hẹn', exact: true }).click();
  await page.getByRole('button', { name: 'Xác nhận hủy lịch', exact: true }).click();
  await expect(page.getByText('Đã hủy', { exact: true })).toBeVisible();

  await page.goto('/notifications');
  await expect(page.getByText('Lịch hẹn đã bị hủy')).toBeVisible();
  await page.getByRole('checkbox', { name: 'Chỉ xem thông báo chưa đọc' }).check();
  await expect(page.getByText('2 thông báo')).toBeVisible();
  await page.getByRole('button', { name: 'Đánh dấu đã đọc' }).first().click();
  await expect(page.getByText('1 thông báo')).toBeVisible();
  await expect(page.getByText('Lịch hẹn đã bị hủy')).toHaveCount(0);

  harness('expire', '--user', 'student');
  await page.reload();
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await login(page, 'other');
  await page.goto('/notifications');
  await expect(page.getByText('Bạn chưa có thông báo nào.')).toBeVisible();
});

test('staff proposal notification is visible to the assigned staff and student', async ({
  page,
  context,
}) => {
  await login(page, 'student');
  const appointmentId = await createBooking(page);
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'staff');

  const proposalPath = `/api/v1/staff/me/appointments/${appointmentId}/change-proposals`;
  const [detail, history, csrf] = await Promise.all([
    context.request.get(`/api/v1/staff/me/appointments/${appointmentId}`),
    context.request.get(proposalPath),
    context.request.get('/api/v1/auth/csrf'),
  ]);
  expect(detail.status()).toBe(200);
  expect(history.status()).toBe(200);
  expect(csrf.status()).toBe(200);
  const { starts_at: startsAt } = (await detail.json()) as { starts_at: string };
  const { context: proposalContext } = (await history.json()) as {
    context: { proposal_version: number; pending_proposal_id: string | null };
  };
  const { csrf_token: csrfToken } = (await csrf.json()) as { csrf_token: string };
  const created = await context.request.post(proposalPath, {
    headers: {
      Origin: 'http://127.0.0.1:3101',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': `notification-proposal-${appointmentId}`,
    },
    data: {
      expected_proposal_version: proposalContext.proposal_version,
      expected_pending_proposal_id: proposalContext.pending_proposal_id,
      proposed_starts_at: new Date(new Date(startsAt).getTime() + 30 * 60_000).toISOString(),
      proposed_location: 'Phòng tư vấn kiểm thử',
    },
  });
  expect(created.status()).toBe(201);

  await page.goto('/staff/notifications');
  await expect(page.getByText('Có đề xuất thay đổi lịch hẹn')).toBeVisible();
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'student');
  await page.goto('/notifications');
  await expect(page.getByText('Có đề xuất thay đổi lịch hẹn')).toBeVisible();

  const { id: proposalId } = (await created.json()) as { id: string };
  harness('expire-proposal', '--id', proposalId);
  await page.getByRole('button', { name: 'Làm mới' }).click();
  await expect(page.getByText('Đề xuất thay đổi lịch hẹn đã hết hạn')).toBeVisible();
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'staff');
  await page.goto('/staff/notifications');
  await expect(page.getByText('Đề xuất thay đổi lịch hẹn đã hết hạn')).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'otherstaff');
  await page.goto('/staff/notifications');
  await expect(page.getByText('Bạn chưa có thông báo nào.')).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await login(page, 'admin');
  const adminInbox = await context.request.get('/api/v1/notifications');
  expect(adminInbox.status()).toBe(403);
});

test('inbox paginates real records and recovers from a failed list request', async ({ page }) => {
  await login(page, 'student');
  const appointmentId = await createBooking(page);
  harness('seed-notification-history', '--id', appointmentId);
  await page.route('**/api/v1/notifications?**', (route) => route.abort('failed'), {
    times: 1,
  });
  await page.goto('/notifications');
  await expect(page.getByRole('button', { name: 'Thử tải lại' })).toBeVisible();
  await page.getByRole('button', { name: 'Thử tải lại' }).click();
  const list = page.getByRole('list', { name: 'Danh sách thông báo' });
  await expect(list.locator('li')).toHaveCount(20);
  await expect(page.getByText('22 thông báo')).toBeVisible();
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(list.locator('li')).toHaveCount(2);
  await expect(page.getByText('Trang 2 / 2')).toBeVisible();
  await page.getByRole('checkbox', { name: 'Chỉ xem thông báo chưa đọc' }).check();
  await expect(page.getByText('Trang 1 / 2')).toBeVisible();
  await expect(list.locator('li')).toHaveCount(20);
});

test('uncertain mark-read result is resolved from the persisted inbox state', async ({ page }) => {
  await login(page, 'student');
  await createBooking(page);
  await page.goto('/notifications');
  await expect(page.getByRole('button', { name: 'Đánh dấu đã đọc' })).toBeVisible();
  harness('arm-loss');
  await page.getByRole('button', { name: 'Đánh dấu đã đọc' }).click();
  await expect(page.getByText('Chưa thể xác nhận trạng thái đã đọc.')).toBeVisible();
  await page.getByRole('button', { name: 'Tải lại trạng thái' }).click();
  await expect(page.getByText('Đã đọc', { exact: true })).toBeVisible();
  await expect(page.getByText('Chưa thể xác nhận trạng thái đã đọc.')).toHaveCount(0);
});
