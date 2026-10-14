import { expect, test, type Page } from '@playwright/test';

async function selectTime(page: Page, purpose = 'Nghề nghiệp và thực tập') {
  await page.getByRole('radio', { name: new RegExp(purpose) }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page
    .getByRole('radio', { name: /Cố vấn Demo/ })
    .first()
    .check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
  await page.getByRole('radio', { name: /09:00 –/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
}

async function chooseScenario(page: Page, scenario: string) {
  await page.getByText('Thiết lập bản demo', { exact: false }).click();
  await page.getByLabel('Kịch bản trải nghiệm').selectOption(scenario);
}

test('books directly without login, renders the mock result, and handles refresh honestly', async ({
  page,
}) => {
  await page.goto('/booking');
  await expect(
    page
      .getByRole('navigation', { name: 'Điều hướng chính' })
      .getByRole('link', { name: 'Đặt lịch tư vấn' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page).toHaveURL('/booking/result');
  await expect(page.getByRole('heading', { name: 'Đặt lịch demo thành công' })).toBeVisible();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();
  await expect(page.getByText('14/10/2026', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Đây là kết quả giả lập. Chưa có lịch hẹn thật nào được tạo.'),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toBeVisible();
});

test('clears dependent staff and time selections when the purpose changes', async ({ page }) => {
  await page.goto('/booking');
  await selectTime(page);
  await page.getByRole('button', { name: /Nhu cầu tư vấn/ }).click();
  await page.getByRole('radio', { name: /Khóa luận và nghiên cứu/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0);
  await page.getByRole('radio', { name: /Cố vấn Demo 02/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
});

test('keeps the latest result when starting another booking and returning through the Results menu', async ({
  page,
}) => {
  await page.goto('/booking');
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();

  await page.getByRole('link', { name: 'Đặt lịch khác' }).click();
  await expect(page.getByRole('heading', { name: 'Chọn nhu cầu tư vấn' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await page.getByRole('link', { name: 'Đăng nhập', exact: true }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Kết quả', exact: true }).click();
  await expect(page).toHaveURL('/booking/result');
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toHaveCount(0);

  await page.getByRole('link', { name: 'Đặt lịch khác' }).click();
  await page.getByText('Thiết lập bản demo', { exact: false }).click();
  await page.getByRole('button', { name: 'Đặt lại demo', exact: true }).click();
  await page.getByRole('link', { name: 'Đăng nhập', exact: true }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Kết quả', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bạn chưa có kết quả đặt lịch.' })).toBeVisible();
});

test('releases a slow submission after client navigation and allows another booking', async ({
  page,
}) => {
  await page.goto('/booking');
  await chooseScenario(page, 'slow');
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('button', { name: 'Đang xác nhận…' })).toBeDisabled();

  await page.getByRole('link', { name: 'Đăng nhập', exact: true }).click();
  await expect(page).toHaveURL('/login');
  await page.getByRole('navigation').getByRole('link', { name: 'Đặt lịch', exact: true }).click();
  await expect(page).toHaveURL('/booking');
  await page.getByText('Thiết lập bản demo', { exact: false }).click();
  const scenario = page.getByLabel('Kịch bản trải nghiệm');
  const reset = page.getByRole('button', { name: 'Đặt lại demo', exact: true });
  // The slow preset survives only when navigation retains the existing demo provider.
  await expect(scenario).toHaveValue('slow');
  await expect(scenario).toBeEnabled();
  await expect(reset).toBeEnabled();

  await selectTime(page);
  await expect(page.getByRole('button', { name: /Quay lại/ })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Xác nhận đặt lịch' })).toBeEnabled();
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('button', { name: 'Đang xác nhận…' })).toBeDisabled();
  await expect(page.getByRole('button', { name: /Quay lại/ })).toBeDisabled();
  await expect(scenario).toBeDisabled();
  await expect(reset).toBeDisabled();
  await expect(page).toHaveURL('/booking/result');
  await expect(page.getByText('Mã lịch hẹn: DEMO-0001')).toBeVisible();

  await page.getByRole('link', { name: 'Đặt lịch khác' }).click();
  await chooseScenario(page, 'success');
  await expect(scenario).toHaveValue('success');
  await page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ }).check();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeEnabled();
  await page.getByText('Thiết lập bản demo', { exact: false }).click();
  await reset.click();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0);
});

test('requires OTHER text and preserves it in review', async ({ page }) => {
  await page.goto('/booking');
  await page.getByRole('radio', { name: /Nhu cầu khác/ }).check();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await page.getByLabel('Bạn muốn trao đổi về điều gì?').fill('  ');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await page.getByLabel('Bạn muốn trao đổi về điều gì?').fill('Kế hoạch học tập kỳ tới');
  await selectTime(page, 'Nhu cầu khác');
  await expect(
    page.getByRole('region', { name: 'Kiểm tra và xác nhận' }).getByText('Kế hoạch học tập kỳ tới'),
  ).toBeVisible();
});

test('recovers from a one-time loading failure using retry', async ({ page }) => {
  await page.goto('/booking');
  await chooseScenario(page, 'purposes-error');
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Chưa thể tải dữ liệu');
  await page.getByRole('button', { name: 'Thử lại' }).click();
  await expect(page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ })).toBeVisible();
});

test('keeps a failed booking on review and never displays success', async ({ page }) => {
  await page.goto('/booking');
  await chooseScenario(page, 'booking-error');
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText(
    'Dịch vụ demo tạm thời không khả dụng',
  );
  await expect(page).toHaveURL('/booking');
  await expect(page.getByRole('heading', { name: 'Đặt lịch demo thành công' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Xác nhận đặt lịch' })).toBeEnabled();
});

test('refreshes after a conflict and completes with another slot and unspecified location', async ({
  page,
}) => {
  await page.goto('/booking');
  await chooseScenario(page, 'conflict-once');
  await selectTime(page);
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Giờ này không còn trống');
  await expect(page.getByRole('radio', { name: /09:00 –/ })).toHaveCount(0);
  await page.getByRole('radio', { name: /09:15 –/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
  await expect(page).toHaveURL('/booking/result');
  await expect(page.getByText('Địa điểm sẽ được thông báo sau', { exact: true })).toBeVisible();
});

test('ignores old staff and slot responses after a newer selection', async ({ page }) => {
  await page.goto('/booking');
  await chooseScenario(page, 'stale-response');
  await page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('radio', { name: /Nhu cầu khác/ }).check();
  await page.getByLabel('Bạn muốn trao đổi về điều gì?').fill('Tư vấn kế hoạch học tập');
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /Cố vấn Demo 04/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
  await page.getByRole('radio', { name: /15 Tháng 10/ }).check();
  await expect(page.getByRole('radio', { name: /10:00 – 10:30/ })).toBeVisible();
  await page.waitForTimeout(1600);
  await expect(page.getByRole('radio', { name: /09:00 –/ })).toHaveCount(0);
  await expect(page.getByRole('radio', { name: /10:00 – 10:30/ })).toBeVisible();
});
