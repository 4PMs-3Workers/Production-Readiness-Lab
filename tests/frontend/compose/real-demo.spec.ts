import { expect, test, type Page } from '@playwright/test';

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for the Compose smoke.`);
  return value;
}

async function signIn(page: Page, email: string, password: string) {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: false }).click();
}

test('student creates, reloads, cancels a real appointment, and logs out', async ({ page }) => {
  const email = required('PRL_SMOKE_STUDENT_EMAIL');
  const password = required('PRL_SMOKE_STUDENT_PASSWORD');
  const staffName = required('PRL_SMOKE_STAFF_NAME');
  const bookingDate = required('PRL_SMOKE_BOOKING_DATE');
  expect(staffName).toBeTruthy();
  expect(bookingDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  await signIn(page, email, password);
  await expect(page).toHaveURL(/\/home$/);

  await page.goto('/booking');
  await page.getByRole('radio', { name: /Định hướng nghề nghiệp và thực tập/ }).check();
  await page.getByRole('button', { name: /Tiếp tục/ }).click();
  await page.getByRole('radio', { name: staffName }).check();
  await page.getByRole('button', { name: /Tiếp tục/ }).click();
  await page.locator(`input[name="date"][value="${bookingDate}"]`).check();
  const slots = page.locator('input[name="slot"]');
  await expect(slots.first()).toBeVisible();
  await slots.first().check();
  await page.getByRole('button', { name: /Tiếp tục/ }).click();

  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/appointments') &&
      response.request().method() === 'POST' &&
      [200, 201].includes(response.status()),
  );
  await page.getByRole('button', { name: /Xác nhận đặt lịch/ }).click();
  const receipt = await created;
  const appointmentId = (await receipt.json()).id as string;
  expect(appointmentId).toMatch(/^[0-9a-f-]{36}$/i);
  await expect(page).toHaveURL(/\/booking\/result\?id=/);
  await expect(page.getByText(`Mã lịch hẹn: ${appointmentId}`)).toBeVisible();

  await page.goto('/appointments');
  const historyItem = page.getByRole('listitem').filter({ hasText: appointmentId });
  await expect(historyItem).toBeVisible();
  await page.reload();
  await expect(page.getByRole('listitem').filter({ hasText: appointmentId })).toBeVisible();
  await page.getByRole('link', { name: `Xem chi tiết lịch ${appointmentId}` }).click();
  await expect(page).toHaveURL(new RegExp(`/appointments/${appointmentId}$`));
  await expect(page.getByText(`Mã lịch hẹn: ${appointmentId}`)).toBeVisible();
  await page.reload();
  await expect(page.getByText(`Mã lịch hẹn: ${appointmentId}`)).toBeVisible();

  const cancelled = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/v1/appointments/${appointmentId}/cancel`) &&
      response.request().method() === 'POST' &&
      response.status() === 200,
  );
  await page.getByRole('button', { name: 'Hủy lịch hẹn' }).click();
  await page.getByRole('button', { name: 'Xác nhận hủy lịch' }).click();
  await cancelled;
  await expect(page.getByRole('status').filter({ hasText: 'Đã hủy' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('status').filter({ hasText: 'Đã hủy' })).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất' }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('Mật khẩu', { exact: true })).toHaveValue('');
});

test('staff opens an assigned persisted appointment in the staff portal', async ({ page }) => {
  const email = required('PRL_SMOKE_STAFF_EMAIL');
  const password = required('PRL_SMOKE_STAFF_PASSWORD');
  const appointmentId = required('PRL_SMOKE_APPOINTMENT_ID');
  await signIn(page, email, password);
  await expect(page).toHaveURL(/\/staff\/appointments$/);
  await expect(page.getByRole('heading', { name: 'Lịch được giao' })).toBeVisible();

  const appointment = page.getByRole('listitem').filter({ hasText: appointmentId });
  await expect(appointment).toContainText('Chờ xác nhận đổi lịch');
  await appointment.getByRole('link', { name: `Xem chi tiết lịch ${appointmentId}` }).click();
  await expect(page).toHaveURL(new RegExp(`/staff/appointments/${appointmentId}$`));
  await expect(
    page.getByRole('heading', { name: 'Định hướng nghề nghiệp và thực tập' }),
  ).toBeVisible();
  await expect(page.getByText('Chờ xác nhận đổi lịch', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Đăng xuất' }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
});
