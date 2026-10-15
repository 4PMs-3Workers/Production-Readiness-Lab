import { expect, test } from './fixtures';

import { chooseScenario, fillLogin, selectTime } from './helpers';

test('keeps every confirmed booking and opens each detail without replacing the latest result', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/login');
  await fillLogin(page);
  await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Đặt lịch tư vấn' }).click();
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();

  await page.getByRole('link', { name: 'Đặt lịch khác' }).click();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await selectTime(page, 'Nghề nghiệp và thực tập', /09:30 –/);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0002')).toBeVisible();
  await page.getByRole('link', { name: 'Xem các lịch đã đặt' }).click();

  const history = page.getByRole('list', { name: 'Các lịch đã đặt trong phiên' });
  await expect(history.getByRole('listitem')).toHaveCount(2);
  await expect(history.getByRole('listitem').nth(0)).toContainText('DEMO-0002');
  await expect(history.getByRole('listitem').nth(1)).toContainText('DEMO-0001');
  await expect(
    page.getByRole('navigation').getByRole('link', { name: 'Lịch đã đặt' }),
  ).toHaveAttribute('aria-current', 'page');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await page.getByRole('link', { name: 'Xem chi tiết lịch DEMO-0001' }).click();
  await expect(page).toHaveURL('/appointments/booking-demo-0001');
  await expect(page.getByRole('heading', { name: 'Chi tiết lịch đã đặt' })).toBeFocused();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();
  await expect(page.getByText('09:00 – 09:30', { exact: true })).toBeVisible();
  await expect(page.getByText('Phòng tư vấn demo A101', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Về danh sách lịch đã đặt' }).click();
  await page.getByRole('link', { name: 'Xem chi tiết lịch DEMO-0002' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0002')).toBeVisible();
  await expect(page.getByText('09:30 – 10:00', { exact: true })).toBeVisible();
  await expect(page.getByText('Địa điểm sẽ được thông báo sau', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await page.getByRole('banner').getByRole('link', { name: 'Trang chủ', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Xem tất cả 2 lịch đã đặt' })).toBeVisible();
  await page.getByRole('link', { name: /Xem kết quả gần nhất/ }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0002')).toBeVisible();
  await page.getByRole('button', { name: 'Đăng xuất' }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Đặt lịch', exact: true }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Lịch đã đặt' }).click();
  await expect(
    page.getByRole('heading', { name: 'Bạn chưa có lịch đã đặt trong phiên.' }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test('clears history on refresh and explains expired booking links', async ({ page }) => {
  await page.goto('/booking');
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await page.getByRole('link', { name: 'Xem các lịch đã đặt' }).click();
  await expect(page.getByRole('listitem')).toHaveCount(1);
  await page.getByRole('link', { name: 'Xem chi tiết lịch DEMO-0001' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Không tìm thấy lịch hẹn trong phiên demo.' }),
  ).toBeVisible();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toHaveCount(0);
  await page.getByRole('link', { name: 'Về danh sách lịch đã đặt' }).click();
  await expect(
    page.getByRole('heading', { name: 'Bạn chưa có lịch đã đặt trong phiên.' }),
  ).toBeVisible();
  await page.goto('/booking/result');
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toBeVisible();
});

for (const action of ['reset', 'scenario'] as const) {
  test(`clears history when the demo ${action} changes its session`, async ({ page }) => {
    await page.goto('/booking');
    await selectTime(page);
    await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
    await page.getByRole('link', { name: 'Đặt lịch khác' }).click();
    if (action === 'reset') {
      await page.getByText('Thiết lập bản demo', { exact: false }).click();
      await page.getByRole('button', { name: 'Đặt lại demo', exact: true }).click();
    } else {
      await chooseScenario(page, 'slow');
    }
    await page.getByRole('navigation').getByRole('link', { name: 'Lịch đã đặt' }).click();
    await expect(
      page.getByRole('heading', { name: 'Bạn chưa có lịch đã đặt trong phiên.' }),
    ).toBeVisible();
    await expect(page.getByRole('link', { name: /Xem chi tiết lịch/ })).toHaveCount(0);
  });
}

test('preserves OTHER notes and student details in both the confirmation and saved detail', async ({
  page,
}) => {
  const notes = 'Cần hỗ trợ kế hoạch học tập.\nTrao đổi về học phần kỳ tới.';
  await page.goto('/booking');
  await page.getByRole('radio', { name: /Nhu cầu khác/ }).check();
  await page.getByLabel('Bạn muốn trao đổi về điều gì?').fill(notes);
  await selectTime(page, 'Nhu cầu khác');
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page).toHaveURL('/booking/result');
  await expect(page.getByText(notes, { exact: true })).toBeVisible();
  await expect(
    page.getByRole('main').getByText('Sinh viên Demo HUS', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('main').getByText('student@example.test', { exact: true }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Xem các lịch đã đặt' }).click();
  await page.getByRole('link', { name: 'Xem chi tiết lịch DEMO-0001' }).click();
  await expect(page.getByText(notes, { exact: true })).toBeVisible();
  await expect(
    page.getByRole('main').getByText('student@example.test', { exact: true }),
  ).toBeVisible();
});

test('completes booking and reaches its history with Tab, Shift+Tab, Space and Enter', async ({
  page,
}) => {
  await page.goto('/booking');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Đến nội dung chính' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('main')).toBeFocused();
  await expect(page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ })).toBeFocused();
  await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Chọn người tư vấn' })).toBeFocused();
  await expect(page.getByRole('radio', { name: /Cố vấn Demo 01/ })).toBeVisible();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: /Quay lại/ })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByRole('radio', { name: /Cố vấn Demo 01/ })).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Chọn ngày & giờ' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('radio', { name: /14 Tháng 10/ })).toBeFocused();
  await page.keyboard.press('Space');
  await expect(page.getByRole('radio', { name: /09:00 –/ })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('radio', { name: /09:00 –/ })).toBeFocused();
  await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Kiểm tra và xác nhận' })).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Xác nhận đặt lịch' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Đặt lịch demo thành công' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Đặt lịch khác' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Xem các lịch đã đặt' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('list', { name: 'Các lịch đã đặt trong phiên' }).getByRole('listitem'),
  ).toHaveCount(1);
});
