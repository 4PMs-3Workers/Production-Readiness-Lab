import type { Page } from '@playwright/test';

export async function selectTime(
  page: Page,
  purpose = 'Nghề nghiệp và thực tập',
  time = /09:00 –/,
) {
  await page.getByRole('radio', { name: new RegExp(purpose) }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page
    .getByRole('radio', { name: /Cố vấn Demo/ })
    .first()
    .check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.getByRole('radio', { name: /14 Tháng 10/ }).check();
  await page.getByRole('radio', { name: time }).check();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
}

export async function chooseScenario(page: Page, scenario: string) {
  await page.getByText('Thiết lập bản demo', { exact: false }).click();
  await page.getByLabel('Kịch bản trải nghiệm').selectOption(scenario);
}

export async function fillLogin(page: Page) {
  await page.getByLabel('Email', { exact: true }).fill('student@example.test');
  await page.getByLabel('Mật khẩu', { exact: true }).fill('demo');
}
