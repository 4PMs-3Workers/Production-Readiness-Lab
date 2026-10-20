import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  StaffAppointmentActions,
  StaffAppointmentDetailView,
  StaffAppointmentList,
} from '@/features/staff/appointments/staff-appointments';
import type { StaffAppointment, StaffAppointmentDetail } from '@/services/staff-contracts';
import { ApiError } from '@/services/api/errors';

const services = vi.hoisted(() => ({
  staffContext: vi.fn(),
  staffAppointments: vi.fn(),
  staffAppointment: vi.fn(),
  cancelStaffAppointment: vi.fn(),
  recordStaffOutcome: vi.fn(),
}));
vi.mock('@/features/api/api-provider', () => ({
  useApi: () => ({
    services,
    session: { status: 'authenticated', user: { id: 'staff-1', role: 'STAFF' } },
  }),
}));

const item: StaffAppointment = {
  id: 'appointment-1',
  purposeId: 'purpose-1',
  purposeCode: 'ACADEMIC',
  purposeName: 'Tư vấn học tập',
  studentId: 'student-1',
  studentName: 'Sinh viên Hư cấu',
  durationMinutes: 45,
  startsAt: '2026-10-20T01:00:00Z',
  endsAt: '2026-10-20T01:45:00Z',
  location: null,
  status: 'UPCOMING',
  createdAt: '2026-10-17T03:00:00Z',
  cancelledAt: null,
  outcomeAt: null,
};
const detail: StaffAppointmentDetail = {
  ...item,
  description: 'Nội dung trao đổi riêng',
  cancelledBy: null,
  cancelReason: null,
  outcomeBy: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  services.staffContext.mockResolvedValue({
    timezone: 'Asia/Ho_Chi_Minh',
    businessDate: '2026-10-20',
  });
  services.staffAppointments.mockResolvedValue({
    items: [item],
    page: 1,
    pageSize: 20,
    total: 21,
  });
  services.staffAppointment.mockResolvedValue(detail);
});

describe('staff assigned appointments', () => {
  it('filters and paginates assigned appointments without rendering private text in summaries', async () => {
    const user = userEvent.setup();
    render(<StaffAppointmentList />);

    expect(await screen.findByText('Sinh viên Hư cấu')).toBeVisible();
    expect(screen.getByText('Tư vấn học tập')).toBeVisible();
    expect(screen.queryByText('Nội dung trao đổi riêng')).not.toBeInTheDocument();
    expect(screen.getAllByText(/Giờ Việt Nam/).length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByLabelText('Trạng thái lịch'), 'UPCOMING');
    fireEvent.change(screen.getByLabelText('Ngày nghiệp vụ'), { target: { value: '2026-10-20' } });
    await user.click(screen.getByRole('button', { name: 'Áp dụng bộ lọc' }));
    await waitFor(() =>
      expect(services.staffAppointments).toHaveBeenLastCalledWith(
        { page: 1, pageSize: 20, status: 'UPCOMING', date: '2026-10-20' },
        { signal: expect.any(AbortSignal) },
      ),
    );
    await user.click(screen.getByRole('button', { name: 'Trang sau' }));
    await waitFor(() =>
      expect(services.staffAppointments).toHaveBeenLastCalledWith(
        { page: 2, pageSize: 20, status: 'UPCOMING', date: '2026-10-20' },
        { signal: expect.any(AbortSignal) },
      ),
    );
  });

  it('loads a deep-linked appointment and displays detail-only consultation text', async () => {
    render(<StaffAppointmentDetailView appointmentId="appointment-1" />);

    expect(await screen.findByText('Nội dung trao đổi riêng')).toBeVisible();
    expect(screen.getByText('Địa điểm sẽ được thông báo sau')).toBeVisible();
    expect(services.staffAppointment).toHaveBeenCalledWith('appointment-1', {
      signal: expect.any(AbortSignal),
    });
  });

  it('does not retain the previous appointment after a detail route changes', async () => {
    const user = userEvent.setup();
    const second = {
      ...detail,
      id: 'appointment-2',
      studentName: 'Sinh viên B',
      description: 'Nội dung riêng B',
    };
    let cancelled = false;
    services.staffAppointment.mockImplementation(async (id: string) =>
      id === 'appointment-2' ? second : { ...detail, status: cancelled ? 'CANCELLED' : 'UPCOMING' },
    );
    services.cancelStaffAppointment.mockImplementation(async () => {
      cancelled = true;
      return { id: item.id, status: 'CANCELLED' };
    });
    const view = render(<StaffAppointmentDetailView appointmentId="appointment-1" />);
    await screen.findByText('Nội dung trao đổi riêng');
    await user.click(screen.getByRole('button', { name: 'Hủy lịch' }));
    await user.click(screen.getByRole('button', { name: 'Xác nhận hủy' }));
    await screen.findByText('Đã hủy');

    view.rerender(<StaffAppointmentDetailView appointmentId="appointment-2" />);
    expect(await screen.findByText('Nội dung riêng B')).toBeVisible();
    expect(screen.queryByText('Nội dung trao đổi riêng')).not.toBeInTheDocument();
    expect(screen.getByText('Sinh viên B')).toBeVisible();
  });

  it('asks for explicit confirmation before recording an outcome', async () => {
    const user = userEvent.setup();
    services.recordStaffOutcome.mockResolvedValue({ id: item.id, status: 'COMPLETED' });
    const reloaded = vi.fn().mockResolvedValue({ ...detail, status: 'COMPLETED' });
    render(<StaffAppointmentActions appointment={detail} onReload={reloaded} />);

    await user.click(screen.getByRole('button', { name: 'Đánh dấu hoàn thành' }));
    expect(screen.getByRole('dialog', { name: 'Xác nhận kết quả buổi gặp' })).toBeVisible();
    expect(services.recordStaffOutcome).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(services.recordStaffOutcome).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Đánh dấu hoàn thành' }));
    await user.click(screen.getByRole('button', { name: 'Xác nhận hoàn thành' }));
    await waitFor(() => expect(services.recordStaffOutcome).toHaveBeenCalledOnce());
  });

  it('requires confirmation and sends the NO_SHOW outcome explicitly', async () => {
    const user = userEvent.setup();
    services.recordStaffOutcome.mockResolvedValue({ id: item.id, status: 'NO_SHOW' });
    render(
      <StaffAppointmentActions
        appointment={detail}
        onReload={vi.fn().mockResolvedValue({ ...detail, status: 'NO_SHOW' })}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Đánh dấu vắng mặt' }));
    expect(services.recordStaffOutcome).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Xác nhận vắng mặt' }));
    await waitFor(() =>
      expect(services.recordStaffOutcome).toHaveBeenCalledWith(item.id, 'NO_SHOW', {
        signal: expect.any(AbortSignal),
        expectedUserId: 'staff-1',
      }),
    );
  });

  it('requires cancellation confirmation and reloads before reporting a completed mutation', async () => {
    const user = userEvent.setup();
    const reloaded = vi.fn().mockResolvedValue({ ...detail, status: 'CANCELLED' });
    services.cancelStaffAppointment.mockResolvedValue({
      id: item.id,
      status: 'CANCELLED',
      cancelled_at: '2026-10-20T00:00:00Z',
    });
    render(<StaffAppointmentActions appointment={detail} onReload={reloaded} />);

    await user.click(screen.getByRole('button', { name: 'Hủy lịch' }));
    const dialog = screen.getByRole('dialog', { name: 'Xác nhận hủy lịch' });
    await user.type(screen.getByLabelText('Lý do hủy (không bắt buộc)'), 'Sinh viên đề nghị');
    await user.click(screen.getByRole('button', { name: 'Xác nhận hủy' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Đã hủy lịch'));
    expect(services.cancelStaffAppointment).toHaveBeenCalledWith(item.id, 'Sinh viên đề nghị', {
      signal: expect.any(AbortSignal),
      expectedUserId: 'staff-1',
    });
    expect(reloaded).toHaveBeenCalledOnce();
    expect(dialog).not.toBeVisible();
  });

  it('moves focus into the cancellation dialog and restores it when the dialog closes', async () => {
    const user = userEvent.setup();
    render(<StaffAppointmentActions appointment={detail} onReload={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Hủy lịch' });
    await user.click(trigger);
    const reason = screen.getByLabelText('Lý do hủy (không bắt buộc)');

    expect(reason).toHaveFocus();
    const confirm = screen.getByRole('button', { name: 'Xác nhận hủy' });
    await user.keyboard('{Shift>}{Tab}{/Shift}');
    expect(confirm).toHaveFocus();
    await user.keyboard('{Tab}');
    expect(reason).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('does not report success when a timed-out outcome is not confirmed by a fresh detail read', async () => {
    const user = userEvent.setup();
    const reloaded = vi.fn().mockResolvedValue(detail);
    services.recordStaffOutcome.mockRejectedValue(new ApiError('TIMEOUT'));
    render(<StaffAppointmentActions appointment={detail} onReload={reloaded} />);

    await user.click(screen.getByRole('button', { name: 'Đánh dấu hoàn thành' }));
    await user.click(screen.getByRole('button', { name: 'Xác nhận hoàn thành' }));

    await waitFor(() => expect(reloaded).toHaveBeenCalledOnce());
    expect(screen.getByRole('alert')).toHaveTextContent(/thử lại|kiểm tra/i);
    expect(screen.queryByText('Đã cập nhật kết quả')).not.toBeInTheDocument();
  });

  it('shows the server outcome timing rule when the appointment has not ended yet', async () => {
    const user = userEvent.setup();
    services.recordStaffOutcome.mockRejectedValue(new ApiError('APPOINTMENT_NOT_ENDED', 409));
    render(<StaffAppointmentActions appointment={detail} onReload={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Đánh dấu hoàn thành' }));
    await user.click(screen.getByRole('button', { name: 'Xác nhận hoàn thành' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('buổi hẹn kết thúc');
    expect(screen.queryByText('Đã cập nhật kết quả')).not.toBeInTheDocument();
  });

  it('disables duplicate outcome submission while the server request is pending', async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    services.recordStaffOutcome.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const reloaded = vi.fn().mockResolvedValue({ ...detail, status: 'COMPLETED' });
    render(<StaffAppointmentActions appointment={detail} onReload={reloaded} />);

    const complete = screen.getByRole('button', { name: 'Đánh dấu hoàn thành' });
    await user.click(complete);
    await user.click(screen.getByRole('button', { name: 'Xác nhận hoàn thành' }));
    expect(complete).toBeDisabled();
    finish({ id: item.id, status: 'COMPLETED', outcome_at: 'later' });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('Đã cập nhật kết quả'),
    );
    expect(reloaded).toHaveBeenCalledOnce();
  });
});
