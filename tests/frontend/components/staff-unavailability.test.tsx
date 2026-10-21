import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffUnavailabilityManager } from '@/features/staff/unavailability/staff-unavailability';
import { ApiError } from '@/services/api/errors';

const services = vi.hoisted(() => ({
  staffContext: vi.fn(),
  staffUnavailability: vi.fn(),
  createStaffUnavailability: vi.fn(),
  deleteStaffUnavailability: vi.fn(),
}));
vi.mock('@/features/api/api-provider', () => ({
  useApi: () => ({
    services,
    session: { status: 'authenticated', user: { id: 'staff-1', role: 'STAFF' } },
  }),
}));

const period = {
  id: 'period-1',
  startsAt: '2026-10-20T08:00:00Z',
  endsAt: '2026-10-21T08:00:00Z',
  createdAt: '2026-10-17T03:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  services.staffContext.mockResolvedValue({
    timezone: 'Asia/Ho_Chi_Minh',
    businessDate: '2026-10-20',
  });
  services.staffUnavailability.mockResolvedValue({
    items: [period],
    page: 1,
    pageSize: 20,
    total: 1,
  });
  services.createStaffUnavailability.mockResolvedValue(period);
  services.deleteStaffUnavailability.mockResolvedValue(undefined);
});

function enterCrossDayInterval() {
  fireEvent.change(screen.getByLabelText('Bắt đầu'), {
    target: { value: '2026-10-20T23:00' },
  });
  fireEvent.change(screen.getByLabelText('Kết thúc'), {
    target: { value: '2026-10-21T02:00' },
  });
}

describe('staff unavailability', () => {
  it('creates an offset-bearing cross-day interval and reloads the list', async () => {
    const user = userEvent.setup();
    render(<StaffUnavailabilityManager />);
    expect(await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' })).toBeVisible();
    expect(screen.getAllByText(/Giờ Việt Nam/).length).toBeGreaterThan(0);
    enterCrossDayInterval();
    await user.click(screen.getByRole('button', { name: 'Thêm khoảng thời gian' }));

    await waitFor(() =>
      expect(services.createStaffUnavailability).toHaveBeenCalledWith(
        '2026-10-20T23:00:00+07:00',
        '2026-10-21T02:00:00+07:00',
        { signal: expect.any(AbortSignal), expectedUserId: 'staff-1' },
      ),
    );
    expect(await screen.findByText('Đã thêm khoảng thời gian không nhận lịch.')).toBeVisible();
    expect(services.staffUnavailability).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Lịch hẹn hiện có không bị xóa/)).toBeVisible();
  });

  it('keeps input after a conflict and gives a clear corrective message', async () => {
    const user = userEvent.setup();
    services.createStaffUnavailability.mockRejectedValue(
      new ApiError('UNAVAILABILITY_CONFLICT', 409),
    );
    render(<StaffUnavailabilityManager />);
    await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' });
    enterCrossDayInterval();
    await user.click(screen.getByRole('button', { name: 'Thêm khoảng thời gian' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('đang có lịch hẹn');
    expect(screen.getByLabelText('Bắt đầu')).toHaveValue('2026-10-20T23:00');
    expect(screen.getByLabelText('Kết thúc')).toHaveValue('2026-10-21T02:00');
  });

  it('retries an unknown create with the exact same normalized interval', async () => {
    const user = userEvent.setup();
    services.createStaffUnavailability
      .mockRejectedValueOnce(new ApiError('TIMEOUT'))
      .mockResolvedValueOnce(period);
    render(<StaffUnavailabilityManager />);
    await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' });
    enterCrossDayInterval();
    await user.click(screen.getByRole('button', { name: 'Thêm khoảng thời gian' }));
    await user.click(await screen.findByRole('button', { name: 'Thử lại khoảng này' }));

    await waitFor(() => expect(services.createStaffUnavailability).toHaveBeenCalledTimes(2));
    expect(services.createStaffUnavailability.mock.calls[0].slice(0, 2)).toEqual(
      services.createStaffUnavailability.mock.calls[1].slice(0, 2),
    );
  });

  it('unlocks the same interval for correction after an exact replay is definitively rejected', async () => {
    const user = userEvent.setup();
    services.createStaffUnavailability
      .mockRejectedValueOnce(new ApiError('TIMEOUT'))
      .mockRejectedValueOnce(new ApiError('UNAVAILABILITY_CONFLICT', 409));
    render(<StaffUnavailabilityManager />);
    await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' });
    enterCrossDayInterval();
    await user.click(screen.getByRole('button', { name: 'Thêm khoảng thời gian' }));
    await user.click(await screen.findByRole('button', { name: 'Thử lại khoảng này' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('đang có lịch hẹn');
    expect(screen.getByLabelText('Bắt đầu')).toBeEnabled();
    expect(screen.getByLabelText('Kết thúc')).toBeEnabled();
    expect(screen.getByLabelText('Bắt đầu')).toHaveValue('2026-10-20T23:00');
    expect(screen.getByRole('button', { name: 'Thêm khoảng thời gian' })).toBeVisible();
  });

  it('keeps an unresolved interval through a CSRF rejection before exact replay', async () => {
    const user = userEvent.setup();
    const periods: (typeof period)[] = [];
    let creationAudits = 0;
    services.createStaffUnavailability
      .mockImplementationOnce(async () => {
        periods.push(period);
        creationAudits += 1;
        throw new ApiError('TIMEOUT');
      })
      .mockRejectedValueOnce(new ApiError('INVALID_CSRF', 403))
      .mockImplementationOnce(async () => periods[0]);
    render(<StaffUnavailabilityManager />);
    await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' });
    enterCrossDayInterval();
    await user.click(screen.getByRole('button', { name: 'Thêm khoảng thời gian' }));
    await user.click(await screen.findByRole('button', { name: 'Thử lại khoảng này' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Phiên bảo vệ yêu cầu');
    expect(screen.getByLabelText('Bắt đầu')).toBeDisabled();
    expect(screen.getByLabelText('Kết thúc')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Thử lại khoảng này' }));

    await waitFor(() => expect(services.createStaffUnavailability).toHaveBeenCalledTimes(3));
    expect(services.createStaffUnavailability.mock.calls.map((call) => call.slice(0, 2))).toEqual([
      ['2026-10-20T23:00:00+07:00', '2026-10-21T02:00:00+07:00'],
      ['2026-10-20T23:00:00+07:00', '2026-10-21T02:00:00+07:00'],
      ['2026-10-20T23:00:00+07:00', '2026-10-21T02:00:00+07:00'],
    ]);
    expect(await screen.findByText('Đã thêm khoảng thời gian không nhận lịch.')).toBeVisible();
    expect(periods).toHaveLength(1);
    expect(creationAudits).toBe(1);
  });

  it('filters owned periods by an offset-bearing business-time interval', async () => {
    const user = userEvent.setup();
    render(<StaffUnavailabilityManager />);
    await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' });
    fireEvent.change(screen.getByLabelText('Lọc từ'), {
      target: { value: '2026-10-20T23:00' },
    });
    fireEvent.change(screen.getByLabelText('Lọc đến'), {
      target: { value: '2026-10-21T02:00' },
    });
    await user.click(screen.getByRole('button', { name: 'Áp dụng bộ lọc' }));

    await waitFor(() =>
      expect(services.staffUnavailability).toHaveBeenLastCalledWith(
        {
          from: '2026-10-20T23:00:00+07:00',
          to: '2026-10-21T02:00:00+07:00',
          page: 1,
          pageSize: 20,
        },
        { signal: expect.any(AbortSignal) },
      ),
    );
  });

  it('rejects an inverted filter interval before requesting periods', async () => {
    const user = userEvent.setup();
    render(<StaffUnavailabilityManager />);
    await screen.findByRole('heading', { name: 'Thời gian không nhận lịch' });
    fireEvent.change(screen.getByLabelText('Lọc từ'), {
      target: { value: '2026-10-21T02:00' },
    });
    fireEvent.change(screen.getByLabelText('Lọc đến'), {
      target: { value: '2026-10-20T23:00' },
    });
    await user.click(screen.getByRole('button', { name: 'Áp dụng bộ lọc' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Lọc đến phải sau Lọc từ');
    expect(services.staffUnavailability).toHaveBeenCalledTimes(1);
  });

  it('refreshes after an unknown delete without claiming success from a paginated list', async () => {
    const user = userEvent.setup();
    services.deleteStaffUnavailability.mockRejectedValue(new ApiError('TIMEOUT'));
    services.staffUnavailability
      .mockResolvedValueOnce({ items: [period], page: 1, pageSize: 20, total: 1 })
      .mockResolvedValueOnce({ items: [], page: 1, pageSize: 20, total: 0 })
      .mockResolvedValue({ items: [], page: 1, pageSize: 20, total: 0 });
    render(<StaffUnavailabilityManager />);
    expect(await screen.findByText(/20\/10\/2026/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Xóa khoảng thời gian' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Chưa xác nhận được kết quả xóa');
    expect(screen.queryByText('Đã xóa khoảng thời gian không nhận lịch.')).not.toBeInTheDocument();
    expect(services.staffUnavailability).toHaveBeenCalledTimes(2);
  });

  it('displays both period dates in the server business timezone', async () => {
    services.staffUnavailability.mockResolvedValue({
      items: [{ ...period, startsAt: '2026-10-20T23:00:00Z', endsAt: '2026-10-21T01:00:00Z' }],
      page: 1,
      pageSize: 20,
      total: 1,
    });
    render(<StaffUnavailabilityManager />);

    expect(
      await screen.findByRole('list', { name: 'Các khoảng không nhận lịch' }),
    ).toHaveTextContent('21/10/2026 · 06:00 — 21/10/2026 · 08:00');
  });
});
