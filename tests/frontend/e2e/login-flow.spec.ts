import { expect, test } from './fixtures';

import { chooseScenario, fillLogin, selectTime } from './helpers';

test('logs in with the keyboard, books from Home, and clears the session on logout', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/login');
  await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
  await expect(page.getByLabel('Email', { exact: true })).toBeFocused();
  await expect(page.getByLabel('Email', { exact: true })).toHaveAttribute('aria-invalid', 'true');
  await fillLogin(page);
  await page.getByLabel('Mật khẩu', { exact: true }).press('Enter');
  await expect(page).toHaveURL('/home');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'Chào mừng, Sinh viên Demo HUS.',
  );
  await expect(
    page
      .getByRole('complementary', { name: 'Thông tin sinh viên' })
      .getByText('student@example.test'),
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('navigation').getByRole('link', { name: 'Đặt lịch tư vấn' }).click();
  await chooseScenario(page, 'success');
  await expect(page.getByRole('button', { name: 'Đăng xuất' })).toBeVisible();
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page).toHaveURL('/booking/result');
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();
  await page.getByRole('banner').getByRole('link', { name: 'Trang chủ', exact: true }).click();
  await expect(page.getByRole('link', { name: /Xem kết quả gần nhất/ })).toBeVisible();
  await page.getByRole('button', { name: 'Đăng xuất' }).click();
  await expect(page).toHaveURL('/login');
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('Mật khẩu', { exact: true })).toHaveValue('');
  await page.getByRole('navigation').getByRole('link', { name: 'Kết quả', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toBeVisible();
  expect(errors).toEqual([]);
});

for (const [scenario, message] of [
  ['invalid-login', 'Thông tin đăng nhập demo không hợp lệ.'],
  ['login-error', 'Dịch vụ demo tạm thời không khả dụng.'],
]) {
  test(`shows ${scenario} and allows a successful retry`, async ({ page }) => {
    await page.goto('/login');
    await chooseScenario(page, scenario);
    await fillLogin(page);
    await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
    const error = page.getByRole('form', { name: 'Đăng nhập demo' }).getByRole('alert');
    await expect(error).toContainText(message);
    await expect(error).toBeFocused();
    await expect(page.getByLabel('Mật khẩu', { exact: true })).toHaveValue('');
    await expect(page).toHaveURL('/login');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await chooseScenario(page, 'success');
    await fillLogin(page);
    await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
    await expect(page).toHaveURL('/home');
  });
}

test('cancels slow login through client navigation and can start a new login', async ({ page }) => {
  await page.goto('/login');
  await chooseScenario(page, 'slow');
  await fillLogin(page);
  await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
  await expect(page.getByRole('button', { name: 'Đang đăng nhập…' })).toBeDisabled();
  await expect(page.getByLabel('Kịch bản trải nghiệm')).toBeDisabled();
  await page.getByRole('navigation').getByRole('link', { name: 'Đặt lịch', exact: true }).click();
  await expect(page).toHaveURL('/booking');
  await page.getByRole('link', { name: 'Đăng nhập', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Đăng nhập demo' })).toBeEnabled();
  await page.getByText('Thiết lập bản demo', { exact: false }).click();
  await expect(page.getByLabel('Kịch bản trải nghiệm')).toHaveValue('slow');
  await expect(page.getByLabel('Kịch bản trải nghiệm')).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Đặt lại demo' })).toBeEnabled();
  await fillLogin(page);
  await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
  await expect(page).toHaveURL('/home');
});

test('ends the in-memory session on refresh and still permits direct booking', async ({ page }) => {
  await page.goto('/login');
  await fillLogin(page);
  await page.getByRole('button', { name: 'Đăng nhập demo' }).click();
  await expect(page).toHaveURL('/home');
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Bạn chưa có phiên đăng nhập demo.' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Đăng xuất' })).toHaveCount(0);
  await page.getByRole('link', { name: 'Xem demo đặt lịch' }).click();
  await expect(page.getByRole('heading', { name: 'Chọn nhu cầu tư vấn' })).toBeVisible();
});
