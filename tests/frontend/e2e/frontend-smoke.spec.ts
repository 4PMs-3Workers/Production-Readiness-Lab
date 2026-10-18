import { expect, test } from './fixtures';

test('opens the Vietnamese demo routes and follows navigation without backend services', async ({
  page,
}) => {
  const runtimeErrors: string[] = [];
  page.on('pageerror', (error) => runtimeErrors.push(error.message));

  await page.goto('/');
  await expect(page).toHaveURL('/login');
  await expect(page.locator('html')).toHaveAttribute('lang', 'vi');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'Thêm một điểm tựa trên hành trình đại học.',
  );
  await expect(page.getByText('Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.')).toBeVisible();

  await page.getByRole('link', { name: 'Khám phá luồng đặt lịch' }).click();
  await expect(page).toHaveURL('/booking');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Đặt lịch tư vấn');

  await page.goto('/booking/result');
  await expect(page).toHaveURL('/booking/result');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toBeVisible();
  await page.getByRole('link', { name: 'Quay lại đặt lịch' }).click();
  await expect(page).toHaveURL('/booking');

  expect(runtimeErrors).toEqual([]);
});

test('serves each route directly and lets the keyboard reach its main content', async ({
  page,
}) => {
  for (const route of [
    '/login',
    '/home',
    '/profile',
    '/booking',
    '/booking/result',
    '/appointments',
    '/appointments/unknown',
  ]) {
    const response = await page.goto(route);
    expect(response?.status()).toBe(200);
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Đến nội dung chính' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('main')).toBeFocused();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  }
});
