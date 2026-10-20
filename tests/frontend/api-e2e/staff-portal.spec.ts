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

function shiftDate(date: string, days: number) {
  const value = new Date(`${date}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

async function login(page: Page, account = 'staff') {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page).toHaveURL('/staff/appointments');
  await expect(page.getByRole('heading', { name: 'Lịch được giao' })).toBeVisible();
}

test.beforeEach(() => {
  harness('reset');
});

test('staff can cancel an assigned appointment and record a past appointment outcome', async ({
  page,
}) => {
  const seeded = JSON.parse(harness('seed-staff-appointments')) as {
    cancelId: string;
    outcomeId: string;
  };
  await login(page);
  await expect(page).toHaveURL('/staff/appointments');
  await expect(page.getByRole('heading', { name: 'Lịch được giao' })).toBeVisible();
  await expect(page.getByText(seeded.cancelId, { exact: true })).toBeVisible();
  await expect(page.getByText(seeded.outcomeId, { exact: true })).toBeVisible();
  await page
    .getByRole('link', { name: new RegExp(`Xem chi tiết lịch ${seeded.cancelId}`) })
    .click();
  await page.getByRole('button', { name: 'Hủy lịch', exact: true }).click();
  await page.getByLabel('Lý do hủy (không bắt buộc)').fill('Lý do giả lập');
  await page.getByRole('button', { name: 'Xác nhận hủy', exact: true }).click();
  await expect(page.getByText('Đã hủy lịch', { exact: true })).toBeVisible();
  await expect(page.getByText('Đã hủy', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Đã hủy', { exact: true })).toBeVisible();

  await page.goto(`/staff/appointments/${seeded.outcomeId}`);
  await page.getByRole('button', { name: 'Đánh dấu hoàn thành', exact: true }).click();
  await page.getByRole('button', { name: 'Xác nhận hoàn thành', exact: true }).click();
  await expect(page.getByText('Đã cập nhật kết quả', { exact: true })).toBeVisible();
  await expect(page.getByText('Đã hoàn thành', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Đã hoàn thành', { exact: true })).toBeVisible();
});

test('staff cannot read another staff member appointment and unsupported roles see no staff data', async ({
  page,
}) => {
  const seeded = JSON.parse(harness('seed-staff-appointments')) as { cancelId: string };
  await login(page, 'otherstaff');
  await page.goto(`/staff/appointments/${seeded.cancelId}`);
  await expect(page.getByText(/Không tìm thấy dữ liệu/)).toBeVisible();
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);

  for (const [index, account] of ['student', 'admin'].entries()) {
    if (index > 0) {
      await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
      await expect(page).toHaveURL(/\/login/);
    }
    await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
    await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
    await page.goto('/staff/appointments');
    await expect(page.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
    await expect(page.getByText(seeded.cancelId, { exact: true })).toHaveCount(0);
  }
});

test('an expired staff session hides private detail and a new account cannot reuse it', async ({
  page,
}) => {
  const seeded = JSON.parse(harness('seed-staff-appointments')) as { cancelId: string };
  await login(page);
  await page.goto(`/staff/appointments/${seeded.cancelId}`);
  await expect(page.getByRole('heading', { name: 'Tư vấn học tập kiểm thử' })).toBeVisible();
  harness('expire', '--user', 'staff');
  await page.reload();
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await expect(page.getByRole('heading', { name: 'Tư vấn học tập kiểm thử' })).toHaveCount(0);

  await page.getByLabel('Email', { exact: true }).fill('otherstaff@frontend.example.invalid');
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page).toHaveURL(`/staff/appointments/${seeded.cancelId}`);
  await expect(page.getByText(/Không tìm thấy dữ liệu/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tư vấn học tập kiểm thử' })).toHaveCount(0);
});

test('purpose preferences are saved and unavailability uses business time and exact replay', async ({
  page,
}) => {
  const seeded = JSON.parse(harness('seed-staff-appointments')) as {
    futureDate: string;
    nextDate: string;
  };
  await login(page);
  await page.goto('/staff/preferences');
  const accepting = page.getByRole('switch', { name: 'Ngừng nhận Tư vấn học tập kiểm thử' });
  await expect(accepting).toBeChecked();
  await accepting.click();
  await expect(
    page.getByRole('switch', { name: 'Bật nhận Tư vấn học tập kiểm thử' }),
  ).toHaveAttribute('aria-checked', 'false');
  await page.reload();
  await expect(
    page.getByRole('switch', { name: 'Bật nhận Tư vấn học tập kiểm thử' }),
  ).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByText(/không hủy hoặc thay đổi các lịch hẹn hiện có/)).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel('Email', { exact: true }).fill('student@frontend.example.invalid');
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
  await page.goto('/booking');
  await page.getByRole('radio', { name: /Tư vấn học tập kiểm thử/ }).check();
  await page.getByLabel('Bạn muốn trao đổi về điều gì? *').fill('Nội dung giả lập.');
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByText('Hiện chưa có lựa chọn phù hợp.')).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
  await login(page);

  await page.goto('/staff/unavailability');
  await page.getByLabel('Bắt đầu').fill(`${seeded.futureDate}T09:30`);
  await page.getByLabel('Kết thúc').fill(`${seeded.futureDate}T10:30`);
  await page.getByRole('button', { name: 'Thêm khoảng thời gian', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('đang có lịch hẹn');
  await expect(page.getByLabel('Bắt đầu')).toHaveValue(`${seeded.futureDate}T09:30`);

  await page.getByLabel('Bắt đầu').fill(`${seeded.nextDate}T23:00`);
  await page.getByLabel('Kết thúc').fill(`${shiftDate(seeded.nextDate, 1)}T02:00`);
  harness('arm-loss');
  await page.getByRole('button', { name: 'Thêm khoảng thời gian', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Thử lại khoảng này', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Thử lại khoảng này', exact: true }).click();
  await expect(page.getByText('Đã thêm khoảng thời gian không nhận lịch.')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Các khoảng không nhận lịch' })).toContainText(
    'Asia/Ho_Chi_Minh',
  );
  await page.reload();
  await expect(page.getByRole('list', { name: 'Các khoảng không nhận lịch' })).toContainText(
    'Asia/Ho_Chi_Minh',
  );
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await login(page, 'otherstaff');
  await page.goto('/staff/unavailability');
  await expect(page.getByText('Chưa có khoảng thời gian nào.')).toBeVisible();
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await login(page);
  await page.goto('/staff/unavailability');
  await page.getByRole('button', { name: 'Xóa khoảng thời gian' }).click();
  await expect(page.getByText('Đã xóa khoảng thời gian không nhận lịch.')).toBeVisible();
});
