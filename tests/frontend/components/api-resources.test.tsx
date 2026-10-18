import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiProvider } from '@/features/api/api-provider';
import { ApiDateStep, ApiStaffStep } from '@/features/api/api-booking-fields';
import { ApiStudentProfile } from '@/features/api/api-student-profile';
import { ApiServices } from '@/services/api/client';
import type { Purpose } from '@/types/booking';

const purpose: Purpose = {
  id: 'a',
  code: 'SERVER_DEFINED',
  name: 'Purpose A',
  durationMinutes: 45,
  requiresFreeText: false,
};
const user = {
  id: 'student',
  name: 'Fictional Student',
  email: 'student@example.invalid',
  role: 'STUDENT',
};

describe('API-dependent resources', () => {
  it('shows missing profile fields without inventing student data', async () => {
    const services = new ApiServices(
      vi.fn<typeof fetch>(async (path) =>
        Response.json(
          String(path).endsWith('/auth/me')
            ? user
            : {
                ...user,
                student_code: null,
                faculty: null,
                major: null,
                gender: null,
              },
        ),
      ),
    );
    render(
      <ApiProvider services={services}>
        <ApiStudentProfile />
      </ApiProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Thông tin sinh viên', level: 1 }),
    ).toBeVisible();
    expect(screen.getAllByText('Chưa cập nhật', { exact: true })).toHaveLength(4);
    expect(screen.getByText('student@example.invalid', { exact: true })).toBeVisible();
  });

  it('rejects stale staff results through A → B → A, even when transport ignores abort', async () => {
    const requests: { resolve: (value: Response) => void; signal: AbortSignal }[] = [];
    const services = new ApiServices(
      vi.fn<typeof fetch>((path, init) =>
        String(path).endsWith('/me')
          ? Promise.resolve(Response.json(user))
          : new Promise((resolve) =>
              requests.push({ resolve, signal: init!.signal as AbortSignal }),
            ),
      ),
    );
    const view = (value: Purpose) => (
      <ApiProvider services={services}>
        <ApiStaffStep purpose={value} selected={null} onSelect={() => {}} />
      </ApiProvider>
    );
    const { rerender } = render(view(purpose));
    rerender(view({ ...purpose, id: 'b' }));
    rerender(view(purpose));
    expect(requests).toHaveLength(3);
    expect(requests[0].signal.aborted).toBe(true);
    expect(requests[1].signal.aborted).toBe(true);
    const page = (name: string) =>
      Response.json({ items: [{ id: name, name }], page: 1, page_size: 20, total: 1 });
    await act(async () => {
      requests[0].resolve(page('Old A'));
      requests[1].resolve(page('Old B'));
    });
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    await act(async () => requests[2].resolve(page('Current A')));
    expect(await screen.findByRole('radio', { name: /Current A/ })).toBeVisible();
    expect(screen.queryByText('Old A')).not.toBeInTheDocument();
    expect(screen.queryByText('Old B')).not.toBeInTheDocument();
  });

  it('shows only D+1 through D+7 for the standard seven-day server window', async () => {
    const services = new ApiServices(vi.fn<typeof fetch>(async () => Response.json(user)));
    render(
      <ApiProvider services={services}>
        <ApiDateStep
          selection={{
            purpose,
            staff: { id: 's', displayName: 'Advisor' },
            date: '2026-10-24',
            slot: null,
            description: '',
          }}
          context={{
            timezone: 'Asia/Ho_Chi_Minh',
            businessDate: '2026-10-17',
            minBookingDate: '2026-10-18',
            maxBookingDate: '2026-10-24',
          }}
          refresh={0}
          onDate={() => {}}
          onSlot={() => {}}
          onContextRefresh={() => {}}
        />
      </ApiProvider>,
    );
    expect(screen.getAllByRole('radio')).toHaveLength(7);
    expect(screen.getByRole('radio', { name: '18/10/2026' })).toBeVisible();
    expect(screen.getByRole('radio', { name: '24/10/2026' })).toBeChecked();
    expect(screen.queryByRole('radio', { name: '25/10/2026' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tuần sau' })).not.toBeInTheDocument();
  });

  it('supports a non-default server window without exposing dates beyond its bounds', async () => {
    const services = new ApiServices(vi.fn<typeof fetch>(async () => Response.json(user)));
    const onDate = vi.fn();
    render(
      <ApiProvider services={services}>
        <ApiDateStep
          selection={{
            purpose,
            staff: { id: 's', displayName: 'Advisor' },
            date: '',
            slot: null,
            description: '',
          }}
          context={{
            timezone: 'Pacific/Auckland',
            businessDate: '2026-10-17',
            minBookingDate: '2026-10-18',
            maxBookingDate: '2026-11-16',
          }}
          refresh={0}
          onDate={onDate}
          onSlot={() => {}}
          onContextRefresh={() => {}}
        />
      </ApiProvider>,
    );
    expect(screen.getByRole('radio', { name: '18/10/2026' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tuần trước' })).toBeDisabled();
    const controls = userEvent.setup();
    for (let index = 0; index < 4; index++)
      await controls.click(screen.getByRole('button', { name: 'Tuần sau' }));
    expect(screen.getAllByRole('radio')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Tuần sau' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Tuần trước' })).toBeEnabled();
    await controls.click(screen.getByRole('radio', { name: '16/11/2026' }));
    expect(onDate).toHaveBeenCalledWith('2026-11-16');
    await controls.click(screen.getByRole('button', { name: 'Tuần trước' }));
    expect(screen.getByRole('radio', { name: '08/11/2026' })).toBeVisible();
  });
});
