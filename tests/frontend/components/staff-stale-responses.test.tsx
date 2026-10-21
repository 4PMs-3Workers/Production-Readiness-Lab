import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffAppointmentList } from '@/features/staff/appointments/staff-appointments';
import type { StaffAppointment } from '@/services/staff-contracts';

const requests = vi.hoisted(() => ({
  calls: [] as { resolve: (value: unknown) => void; signal: AbortSignal }[],
  services: {
    staffContext: vi.fn(),
    staffAppointments: vi.fn(),
  },
}));
vi.mock('@/features/api/api-provider', () => ({ useApi: () => ({ services: requests.services }) }));

const appointment = (studentName: string): StaffAppointment => ({
  id: studentName,
  purposeId: 'purpose-1',
  purposeCode: 'ACADEMIC',
  purposeName: 'Tư vấn học tập',
  studentId: studentName,
  studentName,
  durationMinutes: 45,
  startsAt: '2026-10-20T01:00:00Z',
  endsAt: '2026-10-20T01:45:00Z',
  location: null,
  status: 'UPCOMING',
  createdAt: '2026-10-17T03:00:00Z',
  cancelledAt: null,
  outcomeAt: null,
});

beforeEach(() => {
  requests.calls = [];
  requests.services.staffContext.mockResolvedValue({
    timezone: 'Asia/Ho_Chi_Minh',
    businessDate: '2026-10-20',
  });
  requests.services.staffAppointments.mockImplementation((_query, options) => {
    return new Promise((resolve) => {
      requests.calls.push({ resolve, signal: options.signal });
    });
  });
});

describe('staff appointment request ownership', () => {
  it('aborts and ignores an older filter response after a newer filter result is shown', async () => {
    render(<StaffAppointmentList />);
    await waitFor(() => expect(requests.calls).toHaveLength(1));
    await act(async () =>
      requests.calls[0].resolve({
        items: [appointment('Ban đầu')],
        page: 1,
        page_size: 20,
        total: 1,
      }),
    );
    expect((await screen.findAllByText('Ban đầu')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('Trạng thái lịch'), {
      target: { value: 'UPCOMING' },
    });
    await waitFor(() => expect(requests.calls).toHaveLength(2));
    fireEvent.change(screen.getByLabelText('Trạng thái lịch'), {
      target: { value: 'CANCELLED' },
    });
    await waitFor(() => expect(requests.calls).toHaveLength(3));
    expect(requests.calls[1].signal.aborted).toBe(true);

    await act(async () =>
      requests.calls[2].resolve({
        items: [appointment('Kết quả mới nhất')],
        page: 1,
        page_size: 20,
        total: 1,
      }),
    );
    expect((await screen.findAllByText('Kết quả mới nhất')).length).toBeGreaterThan(0);

    await act(async () =>
      requests.calls[1].resolve({
        items: [appointment('Kết quả cũ')],
        page: 1,
        page_size: 20,
        total: 1,
      }),
    );
    expect(screen.queryByText('Kết quả cũ')).not.toBeInTheDocument();
    expect(screen.getAllByText('Kết quả mới nhất').length).toBeGreaterThan(0);
  });
});
