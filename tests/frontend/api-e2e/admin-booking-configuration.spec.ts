import { expect, test } from '@playwright/test';
import {
  apiWrite,
  confirmed,
  editPurpose,
  fixture,
  harness,
  inspectCatalog,
  login,
  readConfiguration,
  saveConfiguration,
  savePurpose,
  secondAdmin,
  selectStudentSlot,
} from './admin-configuration-helpers';

test.beforeEach(() => harness('reset'));

test('one shared draft saves timezone/horizon/step and hours atomically and updates student availability', async ({
  page,
  context,
}) => {
  await login(page);
  await page.goto('/admin/booking-settings');
  await page.getByLabel('Múi giờ nghiệp vụ').fill('UTC');
  await page.getByLabel('Bước bắt đầu slot (phút)').fill('20');
  await page.getByLabel('Cửa sổ đặt lịch (ngày)').fill('10');
  await page.getByRole('link', { name: 'Giờ phục vụ', exact: true }).click();
  await page.getByLabel('Mở cửa Thứ Hai, khoảng 1').fill('08:00:05');
  await page.getByLabel('Đóng cửa Thứ Hai, khoảng 1').fill('12:00:05');
  await saveConfiguration(page);
  await confirmed(page);
  await page.reload();
  const persisted = await readConfiguration(page);
  expect(persisted.timezone).toBe('UTC');
  expect(persisted.start_step_minutes).toBe(20);
  expect(persisted.booking_horizon_days).toBe(10);
  expect(persisted.windows.find((w) => w.weekday === 1)).toEqual({
    weekday: 1,
    opens_at: '08:00:05',
    closes_at: '12:00:05',
  });
  const student = await context.browser()!.newContext({ baseURL: new URL(page.url()).origin });
  try {
    const tab = await student.newPage();
    await login(tab, 'student');
    await selectStudentSlot(tab);
    await expect(tab.getByRole('button', { name: 'Tuần sau', exact: true })).toBeVisible();
    await expect(tab.getByRole('radio', { name: /08:20 – 08:50/ })).toBeVisible();
    const bookingContext = await (await tab.request.get('/api/v1/booking-context')).json();
    expect(bookingContext.timezone).toBe('UTC');
    const f = fixture();
    const availability = await (
      await tab.request.get('/api/v1/availability', {
        params: { purpose_id: f.purpose, staff_id: f.staff, date: bookingContext.min_booking_date },
      })
    ).json();
    expect(
      Date.parse(availability.slots[1].starts_at) - Date.parse(availability.slots[0].starts_at),
    ).toBe(20 * 60000);
  } finally {
    await student.close();
  }
  expect(inspectCatalog().audit_actions).toEqual(['BOOKING_CONFIGURATION_UPDATED']);
});

test('closing the whole week and reopening one day changes real student slots', async ({
  page,
  context,
}) => {
  await login(page);
  await page.goto('/admin/service-hours');
  await expect(page.getByRole('button', { name: /^Xóa/ })).toHaveCount(7);
  while (await page.getByRole('button', { name: /^Xóa/ }).count())
    await page.getByRole('button', { name: /^Xóa/ }).first().click();
  await page.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click();
  await expect(page.getByText(/Cả tuần sẽ đóng cửa/)).toBeVisible();
  await page.getByRole('button', { name: 'Xác nhận lưu cấu hình', exact: true }).click();
  await confirmed(page);
  expect((await readConfiguration(page)).windows).toHaveLength(0);
  const student = await context.browser()!.newContext({ baseURL: new URL(page.url()).origin });
  try {
    const tab = await student.newPage();
    await login(tab, 'student');
    await selectStudentSlot(tab);
    await expect(
      tab.getByText('Ngày này chưa có giờ trống phù hợp. Vui lòng chọn một ngày khác.'),
    ).toBeVisible();
    const bookingContext = await (await tab.request.get('/api/v1/booking-context')).json();
    const weekday = new Date(`${bookingContext.min_booking_date}T12:00:00Z`).getUTCDay() || 7;
    const day = ['Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ nhật'][
      weekday - 1
    ];
    await page.getByRole('button', { name: `Thêm khoảng ${day}`, exact: true }).click();
    await page.getByLabel(`Mở cửa ${day}, khoảng 1`).fill('09:00:00');
    await page.getByLabel(`Đóng cửa ${day}, khoảng 1`).fill('10:00:00');
    await saveConfiguration(page);
    await confirmed(page);
    await selectStudentSlot(tab);
    await expect(tab.getByRole('radio', { name: /09:00 – 09:30/ })).toBeVisible();
  } finally {
    await student.close();
  }
});

test('a confirmed booking keeps its DB instants and duration when future policy and purpose duration change', async ({
  page,
  context,
}) => {
  await login(page);
  const student = await context.browser()!.newContext({ baseURL: new URL(page.url()).origin });
  try {
    const tab = await student.newPage();
    await login(tab, 'student');
    await selectStudentSlot(tab);
    await tab.getByRole('radio', { name: /08:00 – 08:30/ }).check();
    await tab.getByRole('button', { name: 'Tiếp tục →', exact: true }).click();
    await tab.getByRole('button', { name: 'Xác nhận đặt lịch ✓', exact: true }).click();
    await expect(
      tab.getByRole('heading', { name: 'Chi tiết lịch hẹn', exact: true }),
    ).toBeVisible();
    const id = new URL(tab.url()).searchParams.get('id')!;
    const before = JSON.parse(harness('inspect-admin-appointment', '--id', id));
    await editPurpose(page);
    await page.getByLabel('Thời lượng (phút)').fill('60');
    await savePurpose(page);
    await confirmed(page);
    await page.getByRole('link', { name: 'Quy định đặt lịch', exact: true }).click();
    await page.getByLabel('Múi giờ nghiệp vụ').fill('UTC');
    await page.getByLabel('Bước bắt đầu slot (phút)').fill('20');
    await page.getByRole('link', { name: 'Giờ phục vụ', exact: true }).click();
    const opens = page.getByLabel(/^Mở cửa/),
      closes = page.getByLabel(/^Đóng cửa/);
    for (let i = 0; i < 7; i++) {
      await opens.nth(i).fill('10:00:00');
      await closes.nth(i).fill('12:00:00');
    }
    await saveConfiguration(page);
    await confirmed(page);
    expect(JSON.parse(harness('inspect-admin-appointment', '--id', id))).toEqual(before);
    await tab.reload();
    const detail = await (await tab.request.get(`/api/v1/appointments/${id}`)).json();
    expect(Date.parse(detail.starts_at)).toBe(Date.parse(before.starts_at));
    expect(Date.parse(detail.ends_at)).toBe(Date.parse(before.ends_at));
    expect(detail.duration_minutes).toBe(30);
    await selectStudentSlot(tab);
    await expect(tab.getByRole('radio', { name: /10:00 – 11:00/ })).toBeVisible();
  } finally {
    await student.close();
  }
});

test('hours and settings editors share one revision so a second ADMIN must review the winner', async ({
  page,
  context,
}) => {
  harness('seed-admin');
  await login(page);
  await page.goto('/admin/booking-settings');
  const original = await readConfiguration(page);
  const other = await secondAdmin(page, context);
  try {
    await other.tab.goto('/admin/service-hours');
    await expect(other.tab.getByLabel('Mở cửa Thứ Hai, khoảng 1')).toBeVisible();
    await page.getByLabel('Bước bắt đầu slot (phút)').fill('20');
    await saveConfiguration(page);
    await confirmed(page);
    await other.tab.getByLabel('Mở cửa Thứ Hai, khoảng 1').fill('09:00:00');
    await saveConfiguration(other.tab);
    await expect(other.tab.getByRole('heading', { name: 'Dữ liệu đã thay đổi' })).toBeVisible();
    const persisted = await readConfiguration(page);
    expect(persisted.windows).toEqual(original.windows);
    expect(persisted.start_step_minutes).toBe(20);
    await other.tab.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }).click();
    await other.tab
      .getByRole('button', { name: 'Tôi đã xem kết quả, bắt đầu chỉnh sửa mới' })
      .click();
    await expect(other.tab.getByLabel('Mở cửa Thứ Hai, khoảng 1')).toHaveValue('08:00:00');
    expect(inspectCatalog().audit_actions).toEqual(['BOOKING_CONFIGURATION_UPDATED']);
  } finally {
    await other.browser.close();
  }
});

test('a windows-only update changes the common revision, while matching stale no-op preserves it', async ({
  page,
}) => {
  await login(page);
  const before = await readConfiguration(page);
  const body = {
    timezone: before.timezone,
    start_step_minutes: before.start_step_minutes,
    booking_horizon_days: before.booking_horizon_days,
    max_active_appointments: before.max_active_appointments,
    windows: before.windows,
    expected_updated_at: '2020-01-01T00:00:00.000001Z',
  };
  const noop = await apiWrite(page, 'PUT', '/admin/booking-configuration', body);
  expect(noop.status()).toBe(200);
  expect((await noop.json()).updated_at).toBe(before.updated_at);
  await page.goto('/admin/service-hours');
  await page.getByLabel('Mở cửa Thứ Hai, khoảng 1').fill('08:00:15');
  await saveConfiguration(page);
  await confirmed(page);
  const after = await readConfiguration(page);
  expect(after.updated_at).not.toBe(before.updated_at);
  expect(after.start_step_minutes).toBe(before.start_step_minutes);
  const stale = await apiWrite(page, 'PUT', '/admin/booking-configuration', {
    ...body,
    expected_updated_at: before.updated_at,
  });
  expect(stale.status()).toBe(409);
  const matching = await apiWrite(page, 'PUT', '/admin/booking-configuration', {
    ...body,
    windows: after.windows,
  });
  expect(matching.status()).toBe(200);
  expect((await matching.json()).updated_at).toBe(after.updated_at);
  expect(inspectCatalog().audit_actions).toEqual(['BOOKING_CONFIGURATION_UPDATED']);
});

test('lost configuration receipt is recovered by reading persisted full state without another PUT', async ({
  page,
}) => {
  await login(page);
  await page.goto('/admin/booking-settings');
  await page.getByLabel('Bước bắt đầu slot (phút)').fill('17');
  let puts = 0;
  page.on('request', (r) => {
    if (r.method() === 'PUT' && r.url().endsWith('/admin/booking-configuration')) puts++;
  });
  harness('arm-loss');
  await saveConfiguration(page);
  await expect(page.getByRole('heading', { name: 'Chưa xác định kết quả lưu' })).toBeVisible();
  await page.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }).click();
  await page.getByRole('button', { name: 'Dùng trạng thái đã xác minh' }).click();
  await page.reload();
  expect((await readConfiguration(page)).start_step_minutes).toBe(17);
  expect(puts).toBe(1);
  expect(inspectCatalog().audit_actions).toEqual(['BOOKING_CONFIGURATION_UPDATED']);
});

test('unknown recovery stays locked after a failed read and reviews a subsequent competing change', async ({
  page,
  context,
}) => {
  harness('seed-admin');
  await login(page);
  await page.goto('/admin/booking-settings');
  await page.getByLabel('Bước bắt đầu slot (phút)').fill('17');
  harness('arm-loss');
  await saveConfiguration(page);
  await expect(page.getByRole('heading', { name: 'Chưa xác định kết quả lưu' })).toBeVisible();
  harness('settings-off');
  try {
    await page.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }).click();
    await expect(
      page
        .getByRole('region', { name: 'Xem xét kết quả quản trị' })
        .getByText(/Dịch vụ tạm thời chưa sẵn sàng/),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Lưu cấu hình', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: /Tôi đã xem/ })).toHaveCount(0);
  } finally {
    harness('settings-restore');
  }
  const other = await secondAdmin(page, context);
  try {
    const current = await readConfiguration(other.tab);
    const response = await apiWrite(other.tab, 'PUT', '/admin/booking-configuration', {
      timezone: current.timezone,
      start_step_minutes: current.start_step_minutes,
      booking_horizon_days: 9,
      max_active_appointments: current.max_active_appointments,
      windows: current.windows,
      expected_updated_at: current.updated_at,
    });
    expect(response.status()).toBe(200);
    await page.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }).click();
    await expect(page.getByRole('button', { name: 'Dùng trạng thái đã xác minh' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Tôi đã xem kết quả, bắt đầu chỉnh sửa mới' }).click();
    await expect(page.getByLabel('Cửa sổ đặt lịch (ngày)')).toHaveValue('9');
    expect(inspectCatalog().audit_actions).toHaveLength(2);
  } finally {
    await other.browser.close();
  }
});

test('invalid local and backend values fail without effects and the form supports keyboard and a narrow viewport', async ({
  page,
}) => {
  await login(page);
  await page.goto('/admin/booking-settings');
  await page.getByLabel('Múi giờ nghiệp vụ').focus();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Bước bắt đầu slot (phút)')).toBeFocused();
  await page.getByLabel('Bước bắt đầu slot (phút)').fill('0');
  await page.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click();
  await expect(page.getByText('Nhập số nguyên từ 1 đến 32767.')).toBeVisible();
  await page.getByLabel('Bước bắt đầu slot (phút)').fill('17');
  await page.getByLabel('Múi giờ nghiệp vụ').fill('Not/A_Zone');
  const rejection = page.waitForResponse(
    (r) => r.request().method() === 'PUT' && r.status() === 422,
  );
  await saveConfiguration(page);
  await rejection;
  await expect(page.getByText(/Giá trị hoặc các khoảng giờ chưa hợp lệ/)).toBeVisible();
  expect(inspectCatalog().audit_actions).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('a shared-cookie account change while CSRF preflight waits never dispatches an old configuration', async ({
  page,
  context,
}) => {
  await login(page);
  await page.goto('/admin/booking-settings');
  await page.getByLabel('Bước bắt đầu slot (phút)').fill('17');
  let release!: () => void, intercepted!: () => void;
  const held = new Promise<void>((resolve) => {
    intercepted = resolve;
  });
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/auth/csrf', async (route) => {
    intercepted();
    await gate;
    await route.continue();
  });
  let writes = 0;
  page.on('request', (r) => {
    if (r.method() === 'PUT' && r.url().endsWith('/admin/booking-configuration')) writes++;
  });
  try {
    await saveConfiguration(page);
    await held;
    const tab = await context.newPage();
    await tab.goto('/admin/accounts');
    await tab.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await expect(tab).toHaveURL(/\/login/);
    await login(tab, 'student');
    await page.bringToFront();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page.getByRole('heading', { name: 'Không có quyền quản trị' })).toBeVisible();
    release();
    await expect(page.getByLabel('Bước bắt đầu slot (phút)')).toHaveCount(0);
    expect(writes).toBe(0);
    expect(inspectCatalog().audit_actions).toHaveLength(0);
    await tab.close();
  } finally {
    release();
  }
});
