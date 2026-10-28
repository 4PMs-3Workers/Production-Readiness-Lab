import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiResult } from '@/features/api/api-appointments';
import { ApiProvider } from '@/features/api/api-provider';
import { StaffAppointmentDetailView } from '@/features/staff/appointments/staff-appointments';
import { ApiServices } from '@/services/api/client';

vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams('') }));

const APPOINTMENT = '44444444-4444-4444-8444-444444444444';
const STAFF = '11111111-1111-4111-8111-111111111111';
const STUDENT = '22222222-2222-4222-8222-222222222222';
const PROPOSAL = '55555555-5555-4555-8555-555555555555';
const initial = {
  id: APPOINTMENT,
  purpose_id: 'purpose-a',
  purpose_code: 'ACADEMIC',
  purpose_name: 'Tư vấn học tập',
  student_id: STUDENT,
  student_name: 'Sinh viên Hư cấu',
  staff_id: STAFF,
  staff_name: 'Cố vấn Hư cấu',
  duration_minutes: 30,
  starts_at: '2026-10-30T01:00:00Z',
  ends_at: '2026-10-30T01:30:00Z',
  location: 'Phòng cũ A',
  status: 'UPCOMING',
  created_at: '2026-10-24T02:00:00Z',
  description: null,
  cancelled_at: null,
  cancelled_by: null,
  cancel_reason: null,
  outcome_at: null,
  outcome_by: null,
};
const latest = {
  ...initial,
  starts_at: '2026-10-30T04:00:00Z',
  ends_at: '2026-10-30T04:30:00Z',
  location: 'Phòng mới B',
};
const snapshot = {
  items: [
    {
      id: PROPOSAL,
      appointment_id: APPOINTMENT,
      created_by: STAFF,
      created_version: 1,
      proposed_starts_at: latest.starts_at,
      proposed_ends_at: latest.ends_at,
      proposed_location: latest.location,
      status: 'ACCEPTED',
      created_at: '2026-10-24T03:00:00Z',
      expires_at: '2026-10-25T03:00:00Z',
      resolved_at: '2026-10-24T04:00:00Z',
      resolved_by: STUDENT,
    },
  ],
  page: 1,
  page_size: 20,
  total: 1,
  context: {
    appointment_id: APPOINTMENT,
    appointment_status: 'UPCOMING',
    proposal_version: 1,
    pending_proposal_id: null,
  },
};

function mount(role: 'STUDENT' | 'STAFF', refreshedDetail: () => Promise<Response>) {
  let detailReads = 0;
  let historyReads = 0;
  let postedBody: unknown;
  const fetcher = vi.fn<typeof fetch>(async (path, init) => {
    const url = String(path);
    if (url.endsWith('/auth/me'))
      return Response.json({
        id: role === 'STAFF' ? STAFF : STUDENT,
        name: 'Fictional User',
        email: 'fixture@example.invalid',
        role,
      });
    if (url.endsWith('/auth/csrf'))
      return Response.json({ csrf_token: 'fixture-csrf', user_id: STAFF });
    if (url.endsWith('/booking-context') || url.endsWith('/staff/me/context'))
      return Response.json({
        timezone: 'Asia/Ho_Chi_Minh',
        business_date: '2026-10-24',
        min_booking_date: '2026-10-25',
        max_booking_date: '2026-10-31',
      });
    if (url.includes('/change-proposals?')) {
      historyReads += 1;
      return Response.json(snapshot);
    }
    if (url.endsWith('/change-proposals') && init?.method === 'POST') {
      postedBody = JSON.parse(String(init.body));
      return Response.json({
        id: PROPOSAL,
        appointment_id: APPOINTMENT,
        created_version: 2,
        created_at: '2026-10-24T05:00:00Z',
        expires_at: '2026-10-25T05:00:00Z',
      });
    }
    if (url.endsWith(`/appointments/${APPOINTMENT}`)) {
      detailReads += 1;
      return detailReads === 1 ? Response.json(initial) : refreshedDetail();
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  const view = render(
    <ApiProvider services={new ApiServices(fetcher)}>
      {role === 'STUDENT' ? (
        <ApiResult bookingId={APPOINTMENT} />
      ) : (
        <StaffAppointmentDetailView appointmentId={APPOINTMENT} />
      )}
    </ApiProvider>,
  );
  return { ...view, counts: () => ({ detailReads, historyReads }), body: () => postedBody };
}

beforeEach(() => window.sessionStorage.clear());

describe('first proposal snapshot synchronizes the real portal detail', () => {
  it.each(['STUDENT', 'STAFF'] as const)(
    '%s refreshes an accepted change with matching status before enabling proposal actions',
    async (role) => {
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
      let finish!: (response: Response) => void;
      const held = new Promise<Response>((resolve) => {
        finish = resolve;
      });
      const view = mount(role, () => held);
      try {
        await screen.findByText(initial.location);
        await waitFor(() => expect(view.counts().historyReads).toBe(1));
        // Matching UPCOMING status does not establish freshness. Do not publish
        // the history context or allow a form until its detail refresh completes.
        expect(screen.queryByText('Đã chấp nhận')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Đề xuất đổi lịch' })).not.toBeInTheDocument();
        await waitFor(() => expect(view.counts().detailReads).toBe(2));
        await act(async () => finish(Response.json(latest)));
        expect(await screen.findByText('Đã chấp nhận')).toBeVisible();
        expect(screen.queryByText(initial.location)).not.toBeInTheDocument();
        expect(screen.getAllByText(latest.location).length).toBeGreaterThan(0);

        await act(async () => {
          await vi.advanceTimersByTimeAsync(20_000);
        });
        expect(view.counts()).toEqual({ detailReads: 2, historyReads: 3 });
        expect(screen.queryByText(initial.location)).not.toBeInTheDocument();
        vi.useRealTimers();

        if (role === 'STAFF') {
          const controls = userEvent.setup();
          await controls.click(screen.getByRole('button', { name: 'Đề xuất đổi lịch' }));
          expect(screen.getByLabelText('Giờ đề xuất')).toHaveValue('11:00');
          fireEvent.change(screen.getByLabelText('Giờ đề xuất'), { target: { value: '12:00' } });
          await controls.click(screen.getByRole('button', { name: 'Xem lại đề xuất' }));
          await controls.click(screen.getByRole('button', { name: 'Xác nhận gửi đề xuất' }));
          await waitFor(() =>
            expect(view.body()).toMatchObject({
              expected_proposal_version: 1,
              expected_pending_proposal_id: null,
              proposed_location: latest.location,
              proposed_starts_at: '2026-10-30T12:00:00+07:00',
            }),
          );
        } else {
          expect(screen.getByText('11:00 – 11:30')).toBeVisible();
          expect(screen.queryByText('08:00 – 08:30')).not.toBeInTheDocument();
        }
      } finally {
        view.unmount();
        vi.useRealTimers();
      }
    },
  );

  it.each(['STUDENT', 'STAFF'] as const)(
    '%s keeps the first snapshot unavailable after detail failure and retries synchronization',
    async (role) => {
      const refreshedDetail = vi
        .fn()
        .mockResolvedValueOnce(
          Response.json({ error: { code: 'SERVICE_UNAVAILABLE' } }, { status: 503 }),
        )
        .mockImplementation(async () => Response.json(latest));
      const view = mount(role, refreshedDetail);
      await waitFor(() => expect(refreshedDetail).toHaveBeenCalledTimes(1));
      await screen.findByRole('button', { name: 'Thử lại' });
      expect(screen.queryByText('Đã chấp nhận')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Đề xuất đổi lịch' })).not.toBeInTheDocument();
      await userEvent.setup().click(screen.getByRole('button', { name: 'Thử lại' }));
      expect(await screen.findByText('Đã chấp nhận')).toBeVisible();
      expect(screen.queryByText(initial.location)).not.toBeInTheDocument();
      expect(view.counts()).toEqual({ detailReads: 3, historyReads: 2 });
    },
  );
});
