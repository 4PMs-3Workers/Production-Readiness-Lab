import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffPurposePreferences } from '@/features/staff/preferences/purpose-preferences';

const services = vi.hoisted(() => ({
  staffPurposes: vi.fn(),
  setStaffPurpose: vi.fn(),
}));
vi.mock('@/features/api/api-provider', () => ({
  useApi: () => ({
    services,
    session: { status: 'authenticated', user: { id: 'staff-1', role: 'STAFF' } },
  }),
}));

const purposes = [
  {
    id: 'active-purpose',
    code: 'ACADEMIC',
    name: 'Tư vấn học tập',
    durationMinutes: 45,
    requiresFreeText: false,
    isActive: true,
    isAccepting: true,
  },
  {
    id: 'inactive-purpose',
    code: 'LEGACY',
    name: 'Nhu cầu cũ',
    durationMinutes: 30,
    requiresFreeText: false,
    isActive: false,
    isAccepting: false,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  services.staffPurposes.mockResolvedValue({ items: purposes, page: 1, pageSize: 20, total: 2 });
});

describe('staff purpose preferences', () => {
  it('retains inactive associations, updates acceptance only, and explains appointment history is unchanged', async () => {
    const user = userEvent.setup();
    services.setStaffPurpose.mockResolvedValue({ ...purposes[0], isAccepting: false });
    services.staffPurposes
      .mockResolvedValueOnce({ items: purposes, page: 1, pageSize: 20, total: 2 })
      .mockResolvedValue({
        items: [{ ...purposes[0], isAccepting: false }, purposes[1]],
        page: 1,
        pageSize: 20,
        total: 2,
      });
    render(<StaffPurposePreferences />);

    expect(await screen.findByText('Nhu cầu cũ')).toBeVisible();
    expect(screen.getByText('Không hoạt động')).toBeVisible();
    expect(screen.getByText(/không hủy hoặc thay đổi các lịch hẹn hiện có/)).toBeVisible();
    await user.click(screen.getByRole('switch', { name: 'Ngừng nhận Tư vấn học tập' }));

    await waitFor(() =>
      expect(services.setStaffPurpose).toHaveBeenCalledWith('active-purpose', false, {
        signal: expect.any(AbortSignal),
        expectedUserId: 'staff-1',
      }),
    );
    expect(screen.getByRole('switch', { name: 'Bật nhận Tư vấn học tập' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
    expect(services.staffPurposes).toHaveBeenCalledTimes(2);
  });

  it('disables re-enabling an inactive purpose and preserves the switch when an update fails', async () => {
    const user = userEvent.setup();
    services.setStaffPurpose.mockRejectedValue(new Error('failed'));
    render(<StaffPurposePreferences />);

    expect(await screen.findByText('Nhu cầu cũ')).toBeVisible();
    expect(screen.getByRole('switch', { name: 'Bật nhận Nhu cầu cũ' })).toBeDisabled();
    await user.click(screen.getByRole('switch', { name: 'Ngừng nhận Tư vấn học tập' }));

    expect(await screen.findByRole('alert')).toBeVisible();
    expect(screen.getByRole('switch', { name: 'Ngừng nhận Tư vấn học tập' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('uses the refreshed server state after a completed preference update', async () => {
    const user = userEvent.setup();
    services.setStaffPurpose.mockResolvedValue({ ...purposes[0], isAccepting: false });
    services.staffPurposes
      .mockResolvedValueOnce({ items: purposes, page: 1, pageSize: 20, total: 2 })
      .mockResolvedValueOnce({ items: purposes, page: 1, pageSize: 20, total: 2 });
    render(<StaffPurposePreferences />);
    await screen.findByRole('switch', { name: 'Ngừng nhận Tư vấn học tập' });

    await user.click(screen.getByRole('switch', { name: 'Ngừng nhận Tư vấn học tập' }));

    await waitFor(() => expect(services.staffPurposes).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByRole('switch', { name: 'Ngừng nhận Tư vấn học tập' }),
    ).toHaveAttribute('aria-checked', 'true');
  });
});
