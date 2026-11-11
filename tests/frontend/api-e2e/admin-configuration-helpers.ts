import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, type Page, type BrowserContext } from '@playwright/test';
import type { AdminPurpose } from '../../../src/frontend/services/admin-catalog-contracts';
import type { AdminBookingConfiguration } from '../../../src/frontend/services/admin-configuration-contracts';

export const root = resolve(__dirname, '../../..');
export const password = process.env.FRONTEND_E2E_PASSWORD ?? 'Fictional-e2e-password!42';
export const harness = (action: string, ...args: string[]) =>
  execFileSync(
    process.env.FRONTEND_E2E_PYTHON ?? 'python',
    ['tools/scripts/frontend_e2e_backend.py', action, ...args],
    { cwd: root, env: { ...process.env, PYTHONPATH: 'src/backend' }, encoding: 'utf8' },
  );
export const fixture = () =>
  JSON.parse(readFileSync(resolve(root, 'build/frontend-api/fixture.json'), 'utf8')) as {
    purpose: string;
    staff: string;
    date: string;
  };
export const inspectCatalog = () =>
  JSON.parse(harness('inspect-admin-catalog')) as {
    baseline_matches: boolean;
    purpose_count: number;
    staff_links: number;
    audit_actions: string[];
    metadata_safe: boolean;
  };
export async function login(page: Page, account = 'admin') {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(`${account}@frontend.example.invalid`);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}
export async function secondAdmin(page: Page, context: BrowserContext) {
  const browser = await context.browser()!.newContext({ baseURL: new URL(page.url()).origin });
  const tab = await browser.newPage();
  await login(tab, 'admin2');
  return { browser, tab };
}
export async function readPurpose(page: Page) {
  const response = await page.request.get(`/api/v1/admin/purposes/${fixture().purpose}`);
  expect(response.status()).toBe(200);
  return (await response.json()) as AdminPurpose;
}
export async function readConfiguration(page: Page) {
  const response = await page.request.get('/api/v1/admin/booking-configuration');
  expect(response.status()).toBe(200);
  return (await response.json()) as AdminBookingConfiguration;
}
export async function apiWrite(page: Page, method: 'POST' | 'PUT', path: string, body: unknown) {
  const csrfResponse = await page.request.get('/api/v1/auth/csrf');
  expect(csrfResponse.status()).toBe(200);
  const csrf = await csrfResponse.json();
  return page.request.fetch(`/api/v1${path}`, {
    method,
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': csrf.csrf_token },
    data: body,
  });
}
export const purposeReplace = (purpose: AdminPurpose) => ({
  expected_updated_at: purpose.updated_at,
  name: purpose.name,
  duration_minutes: purpose.duration_minutes,
  requires_free_text: purpose.requires_free_text,
  is_active: purpose.is_active,
});
export async function editPurpose(page: Page) {
  await page.goto('/admin/purposes');
  await page.getByRole('button', { name: 'Sửa E2E_CUSTOM_CODE', exact: true }).click();
  await expect(page.getByLabel('Tên nhu cầu')).toBeVisible();
}
export async function savePurpose(page: Page) {
  await page.getByRole('button', { name: 'Xem lại nhu cầu', exact: true }).click();
  await page.getByRole('button', { name: 'Xác nhận lưu nhu cầu', exact: true }).click();
}
export async function saveConfiguration(page: Page) {
  await page.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click();
  await page.getByRole('button', { name: 'Xác nhận lưu cấu hình', exact: true }).click();
}
export async function confirmed(page: Page) {
  await expect(
    page.getByText('Backend đã xác nhận trạng thái yêu cầu.', { exact: true }),
  ).toBeVisible();
}
export async function selectStudentSlot(page: Page) {
  await page.goto('/booking');
  await page.getByRole('radio', { name: /Tư vấn học tập kiểm thử/ }).check();
  await page.getByLabel('Bạn muốn trao đổi về điều gì? *').fill('Nội dung fixture tự động.');
  await page.getByRole('button', { name: 'Tiếp tục →', exact: true }).click();
  await page
    .getByRole('radio', { name: /Cố vấn kiểm thử/ })
    .first()
    .check();
  await page.getByRole('button', { name: 'Tiếp tục →', exact: true }).click();
  await page
    .getByRole('radio', { name: /^\d{2}\/\d{2}\/\d{4}$/ })
    .first()
    .check();
}
