import type { Page } from '@playwright/test';

import { expect, test } from './fixtures';

import { chooseScenario, selectTime } from './helpers';

async function openResource(page: Page, resource: 'purposes' | 'staff' | 'slots') {
  if (resource === 'purposes') return;
  await page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  if (resource === 'staff') return;
  await page.getByRole('radio', { name: /Cố vấn Demo 01/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
}

for (const [resource, message] of [
  ['purposes', 'Hiện chưa có nhu cầu tư vấn.'],
  ['staff', 'Chưa có cố vấn tiếp nhận nhu cầu này.'],
  ['slots', 'Ngày này chưa có giờ trống phù hợp với lịch của bạn.'],
] as const) {
  test(`shows an honest empty state for ${resource} and blocks progression`, async ({ page }) => {
    await page.goto('/booking');
    await chooseScenario(page, `empty-${resource}`);
    await openResource(page, resource);
    await expect(page.getByRole('main').getByRole('status')).toContainText(message);
    await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Xác nhận đặt lịch' })).toHaveCount(0);
  });
}

for (const resource of ['staff', 'slots'] as const) {
  test(`retries ${resource} loading using the current selection`, async ({ page }) => {
    await page.goto('/booking');
    await chooseScenario(page, `${resource}-error`);
    await openResource(page, resource);
    await expect(page.getByRole('main').getByRole('alert')).toContainText('Chưa thể tải dữ liệu');
    await expect(page.getByRole('main').getByRole('alert')).toBeFocused();
    await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
    await page.getByRole('button', { name: 'Thử lại' }).click();
    const option = page.getByRole('radio', {
      name: resource === 'staff' ? /Cố vấn Demo 01/ : /09:00 –/,
    });
    await expect(option).toBeVisible();
    await expect(option).not.toBeChecked();
    await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
    await option.check();
    await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeEnabled();
    if (resource === 'slots')
      await expect(page.getByRole('radio', { name: /14 Tháng 10/ })).toBeChecked();
  });
}

test('preserves back navigation but clears the slot/date when upstream choices change', async ({
  page,
}) => {
  await page.goto('/booking');
  await selectTime(page);
  await page.getByRole('button', { name: /Người tư vấn/ }).click();
  await expect(page.getByRole('radio', { name: /Cố vấn Demo 01/ })).toBeChecked();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByRole('radio', { name: /14 Tháng 10/ })).toBeChecked();
  await expect(page.getByRole('radio', { name: /09:00 –/ })).toBeChecked();
  await page.getByRole('radio', { name: /15 Tháng 10/ }).check();
  const newSlot = page.getByRole('radio', { name: /10:00 –/ });
  await expect(newSlot).toBeVisible();
  await expect(newSlot).not.toBeChecked();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await newSlot.check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(
    page.getByRole('region', { name: 'Kiểm tra và xác nhận' }).getByText('15/10/2026'),
  ).toBeVisible();
  await page.getByRole('button', { name: /Người tư vấn/ }).click();
  await page.getByRole('radio', { name: /Cố vấn Demo 02/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await expect(
    page.getByText('Chọn một ngày phía trên để xem các giờ tư vấn còn trống.'),
  ).toBeVisible();
});

test('shows loading and blocks progression while each catalogue is pending', async ({ page }) => {
  await page.goto('/booking');
  await chooseScenario(page, 'slow');
  const loading = page.getByRole('main').getByRole('status');
  await expect(loading).toHaveText('Đang tải dữ liệu…');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(loading).toHaveText('Đang tải dữ liệu…');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await page.getByRole('radio', { name: /Cố vấn Demo 01/ }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
  await expect(loading).toHaveText('Đang tải dữ liệu…');
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await page.getByRole('radio', { name: /09:00 –/ }).check();
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeEnabled();
});

test('refetches a previously visited date instead of exposing old slots during A → B → A', async ({
  page,
}) => {
  await page.goto('/booking');
  await chooseScenario(page, 'slow');
  await openResource(page, 'slots');
  const slot = page.getByRole('radio', { name: /09:00 –/ });
  await slot.check();
  await page.getByRole('radio', { name: /15 Tháng 10/ }).check();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
  await expect(page.getByRole('main').getByRole('status')).toHaveText('Đang tải dữ liệu…');
  await expect(slot).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled();
  await slot.check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByRole('heading', { name: 'Kiểm tra và xác nhận' })).toBeVisible();
});

for (const timezoneId of ['UTC', 'America/Los_Angeles']) {
  test.describe(`business time in ${timezoneId}`, () => {
    test.use({ timezoneId });
    test('offers D+1 through D+7 and keeps Vietnamese dates/times in review and result', async ({
      page,
    }) => {
      await page.goto('/booking');
      await page.getByRole('radio', { name: /Nghề nghiệp và thực tập/ }).check();
      await page.getByRole('button', { name: 'Tiếp tục' }).click();
      await page.getByRole('radio', { name: /Cố vấn Demo 01/ }).check();
      await page.getByRole('button', { name: 'Tiếp tục' }).click();
      await expect(page.getByRole('radio', { name: /Tháng 10/ })).toHaveCount(7);
      await expect(page.getByRole('radio', { name: /13 Tháng 10|21 Tháng 10/ })).toHaveCount(0);
      await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
      await expect(page.getByRole('radio', { name: /09:00 – 09:30/ })).toBeVisible();
      await page.getByRole('radio', { name: /20 Tháng 10/ }).check();
      await page.getByRole('radio', { name: /14:15 – 14:45/ }).check();
      await page.getByRole('button', { name: 'Tiếp tục' }).click();
      const review = page.getByRole('region', { name: 'Kiểm tra và xác nhận' });
      await expect(review.getByText('20/10/2026', { exact: true })).toBeVisible();
      await expect(review.getByText('14:15 – 14:45', { exact: true })).toBeVisible();
      await expect(
        review.getByText('Địa điểm sẽ được thông báo sau', { exact: true }),
      ).toBeVisible();
      await page.getByRole('button', { name: 'Xác nhận đặt lịch' }).click();
      await expect(page).toHaveURL('/booking/result');
      await expect(page.getByText('20/10/2026', { exact: true })).toBeVisible();
      await expect(page.getByText('14:15 – 14:45', { exact: true })).toBeVisible();
    });
  });
}
