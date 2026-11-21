import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { AdminSessionGate, safeAdminReturnPath } from '@/features/admin/admin-session-gate';
import { AdminWorkspace } from '@/features/admin/admin-workspace';
import { AdminRecovery } from '@/features/admin/admin-recovery';
import { AdminBookingConfiguration } from '@/features/admin/booking-configuration/admin-booking-configuration';
import { adminActor as actor, deferred } from '../admin-fixtures';
import { configurationFixture } from '../admin-configuration-fixtures';

const context = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('@/features/api/api-provider', () => ({ useApi: () => context.value }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => '/admin/booking-settings',
}));
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
let configuration = structuredClone(configurationFixture);
const actorSession = (generation = actor.session_generation) => ({
  status: 'authenticated',
  user: { ...actor, displayName: actor.name, sessionGeneration: generation },
  verifying: false,
});
beforeEach(() => {
  configuration = structuredClone(configurationFixture);
  fetcher = vi.fn<typeof fetch>(async (path, init) => {
    if (String(path).endsWith('/auth/me')) return Response.json(actor);
    if (String(path).endsWith('/auth/csrf'))
      return Response.json({
        user_id: actor.id,
        session_generation: actor.session_generation,
        csrf_token: 'csrf',
      });
    if (init?.method === 'PUT')
      configuration = {
        ...JSON.parse(String(init.body)),
        updated_at: '2026-11-09T01:00:00.123457Z',
      };
    return Response.json(configuration);
  });
  context.value = {
    services: new ApiServices(fetcher),
    session: actorSession(),
    revision: 0,
    refreshSession: vi.fn(),
    logout: vi.fn(),
  };
});
function Harness({ section = 'settings' }: { section?: 'settings' | 'hours' }) {
  return (
    <AdminSessionGate>
      <AdminWorkspace>
        <AdminRecovery />
        <AdminBookingConfiguration section={section} />
      </AdminWorkspace>
    </AdminSessionGate>
  );
}
const writes = () => fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT');
const settingsReady = () => screen.findByLabelText('Múi giờ nghiệp vụ');
async function changeStep(value = '20') {
  await settingsReady();
  fireEvent.change(screen.getByLabelText('Bước bắt đầu slot (phút)'), { target: { value } });
}
async function confirm() {
  await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình' }));
  await userEvent.click(screen.getByRole('button', { name: 'Xác nhận lưu cấu hình' }));
}

describe('ADMIN booking configuration workspace', () => {
  it('preserves the shared draft across pages and saves the complete policy with second precision', async () => {
    const view = render(<Harness />);
    await changeStep();
    view.rerender(<Harness section="hours" />);
    await screen.findByLabelText('Mở cửa Thứ Hai, khoảng 1');
    expect(screen.getAllByText('Đóng cửa')).toHaveLength(6);
    await confirm();
    await screen.findByText('Backend đã xác nhận trạng thái yêu cầu.');
    expect(writes()).toHaveLength(1);
    expect(JSON.parse(String(writes()[0][1]?.body))).toEqual({
      timezone: configurationFixture.timezone,
      start_step_minutes: 20,
      booking_horizon_days: 7,
      windows: configurationFixture.windows,
      expected_updated_at: configurationFixture.updated_at,
    });
  });
  it('validates cross-field overlap and permits adjacent windows without auto merging', async () => {
    render(<Harness section="hours" />);
    const opening = await screen.findByLabelText('Mở cửa Thứ Hai, khoảng 2');
    fireEvent.change(opening, { target: { value: '09:00:00' } });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình' }));
    expect(screen.getByText(/Các khoảng cùng ngày bị trùng/)).toBeVisible();
    expect(opening).toHaveAttribute('aria-invalid', 'true');
    expect(writes()).toHaveLength(0);
    fireEvent.change(opening, { target: { value: '11:30:15' } });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình' }));
    expect(screen.getByText('Không có thay đổi.')).toBeVisible();
    expect(writes()).toHaveLength(0);
  });
  it('supports closing the whole week with an explicit full-state review', async () => {
    render(<Harness section="hours" />);
    await screen.findByLabelText('Mở cửa Thứ Hai, khoảng 1');
    await userEvent.click(screen.getByRole('button', { name: 'Xóa Thứ Hai, khoảng 2' }));
    await userEvent.click(screen.getByRole('button', { name: 'Xóa Thứ Hai, khoảng 1' }));
    await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình' }));
    expect(screen.getByText(/Cả tuần sẽ đóng cửa/)).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Xác nhận lưu cấu hình' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(JSON.parse(String(writes()[0][1]?.body)).windows).toEqual([]);
  });
  it('blocks invalid integer values and does not silently replace dirty drafts on reload', async () => {
    render(<Harness />);
    await changeStep('0');
    await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình' }));
    expect(screen.getByText('Nhập số nguyên từ 1 đến 32767.')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Tải lại cấu hình' }));
    expect(screen.getByLabelText('Bước bắt đầu slot (phút)')).toHaveValue(0);
    await userEvent.click(screen.getByRole('button', { name: 'Tiếp tục chỉnh sửa' }));
    expect(screen.getByLabelText('Bước bắt đầu slot (phút)')).toHaveValue(0);
    expect(writes()).toHaveLength(0);
  });
  it('blocks repeated submissions and waits for the actual receipt', async () => {
    const held = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method === 'PUT' ? held.promise : original(path, init),
    );
    render(<Harness />);
    await changeStep();
    await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình' }));
    const button = screen.getByRole('button', { name: 'Xác nhận lưu cấu hình' });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(screen.queryByText('Backend đã xác nhận trạng thái yêu cầu.')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lưu cấu hình' })).toBeDisabled();
    await act(async () =>
      held.resolve(Response.json({ ...configuration, start_step_minutes: 20 })),
    );
    await screen.findByText('Backend đã xác nhận trạng thái yêu cầu.');
  });
  it('reviews a stale conflict and never rebases or writes automatically', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method === 'PUT'
        ? Promise.resolve(
            Response.json(
              { error: { code: 'BOOKING_CONFIGURATION_VERSION_CONFLICT' } },
              { status: 409 },
            ),
          )
        : original(path, init),
    );
    render(<Harness />);
    await changeStep();
    await confirm();
    await screen.findByRole('heading', { name: 'Dữ liệu đã thay đổi' });
    configuration = {
      ...configuration,
      booking_horizon_days: 10,
      updated_at: '2026-11-09T02:00:00.123456Z',
    };
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }));
    await screen.findByRole('button', { name: 'Tôi đã xem kết quả, bắt đầu chỉnh sửa mới' });
    expect(writes()).toHaveLength(1);
    await userEvent.click(
      screen.getByRole('button', { name: 'Tôi đã xem kết quả, bắt đầu chỉnh sửa mới' }),
    );
    expect(screen.getByLabelText('Cửa sổ đặt lịch (ngày)')).toHaveValue(10);
    expect(screen.getByLabelText('Bước bắt đầu slot (phút)')).toHaveValue(15);
    expect(writes()).toHaveLength(1);
  });
  it('recovers a lost receipt by comparing every submitted field and windows', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (path, init) => {
      const response = await original(path, init);
      if (init?.method === 'PUT') throw new TypeError('lost after commit');
      return response;
    });
    render(<Harness />);
    await changeStep();
    await confirm();
    await screen.findByRole('heading', { name: 'Chưa xác định kết quả lưu' });
    expect(screen.getByRole('button', { name: 'Lưu cấu hình' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Dùng trạng thái đã xác minh' }),
    );
    expect(writes()).toHaveLength(1);
    expect(screen.getByLabelText('Bước bắt đầu slot (phút)')).toHaveValue(20);
  });
  it('keeps uncertainty locked after recovery reads fail, then replays exactly the original body', async () => {
    const original = fetcher.getMockImplementation()!;
    let failRead = false;
    let attempts = 0;
    fetcher.mockImplementation(async (path, init) => {
      if (String(path).includes('/booking-configuration') && !init?.method && failRead)
        return Response.json({ error: { code: 'SERVICE_UNAVAILABLE' } }, { status: 503 });
      const result = await original(path, init);
      if (init?.method === 'PUT' && ++attempts === 1) throw new TypeError('lost');
      return result;
    });
    render(<Harness />);
    await changeStep();
    await confirm();
    await screen.findByRole('heading', { name: 'Chưa xác định kết quả lưu' });
    failRead = true;
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra trạng thái hiện tại' }));
    const recovery = screen.getByRole('region', { name: 'Xem xét kết quả quản trị' });
    await within(recovery).findByText(/Dịch vụ tạm thời chưa sẵn sàng/);
    expect(screen.queryByRole('button', { name: /Tôi đã xem/ })).not.toBeInTheDocument();
    failRead = false;
    await userEvent.click(screen.getByRole('button', { name: 'Gửi lại đúng yêu cầu cũ' }));
    await screen.findByText('Backend đã xác nhận trạng thái yêu cầu.');
    expect(writes()).toHaveLength(2);
    expect(writes()[0][1]?.body).toBe(writes()[1][1]?.body);
  });
  it('keeps drafts on same-session verification and clears them for a replacement session', async () => {
    const view = render(<Harness />);
    await changeStep('99');
    context.value.session = { ...actorSession(), verifying: true };
    view.rerender(<Harness />);
    context.value.session = actorSession();
    view.rerender(<Harness />);
    expect(screen.getByLabelText('Bước bắt đầu slot (phút)')).toHaveValue(99);
    context.value.session = actorSession('b'.repeat(43));
    view.rerender(<Harness />);
    // The actual transport denies the old session's marker; its old draft is gone.
    expect(screen.queryByDisplayValue('99')).not.toBeInTheDocument();
  });
  it.each(['STUDENT', 'STAFF'])('does not read private configuration for %s', (role) => {
    context.value.session = { ...actorSession(), user: { ...actor, role } };
    render(<Harness />);
    expect(screen.getByRole('heading', { name: 'Không có quyền quản trị' })).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each(['/admin/purposes', '/admin/service-hours', '/admin/booking-settings'])(
    'allows the exact new return path %s',
    (path) => {
      expect(safeAdminReturnPath(path)).toBe(path);
      expect(safeAdminReturnPath(`${path}/evil`)).toBe('/admin/accounts');
    },
  );
});
