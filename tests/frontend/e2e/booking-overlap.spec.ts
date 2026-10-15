import { expect, test } from './fixtures';

import { selectTime } from './helpers';

test('blocks overlapping student appointments across purposes and advisors, then accepts another day', async ({
  page,
}) => {
  await page.goto('/booking');
  await selectTime(page, 'Khóa luận và nghiên cứu');
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();
  await expect(page.getByText('09:00 – 10:00', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Đặt lịch khác' }).click();
  await page.getByRole('radio', { name: /Cố vấn học tập/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /Cố vấn Demo 04/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
  await expect(page.getByRole('status')).toContainText(
    'chưa có giờ trống phù hợp với lịch của bạn',
  );
  await expect(page.getByRole('radio', { name: /09:(00|15|30) –/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await expect(
    page.getByText('Giờ trùng với lịch bạn đã đặt trong phiên sẽ được ẩn.'),
  ).toBeVisible();

  await page.getByRole('radio', { name: /15 Tháng 10/ }).check();
  await page.getByRole('radio', { name: /10:00 – 10:30/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0002')).toBeVisible();
  await page.getByRole('link', { name: 'Xem các lịch đã đặt' }).click();
  const bookings = page
    .getByRole('list', { name: 'Các lịch đã đặt trong phiên' })
    .getByRole('listitem');
  await expect(bookings).toHaveCount(2);
  await expect(bookings.nth(0)).toContainText('15/10/2026');
  await expect(bookings.nth(1)).toContainText('14/10/2026');
});
