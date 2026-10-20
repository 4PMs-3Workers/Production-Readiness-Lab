// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { ApiError } from '@/services/api/errors';

const json = (value: unknown, status = 200) => Response.json(value, { status });
const staffUser = {
  id: 'staff-1',
  name: 'Nhân viên mẫu',
  email: 'staff@example.test',
  role: 'STAFF',
};
const staffOptions = { expectedUserId: staffUser.id };
const staffAppointment = {
  id: 'appointment-1',
  purpose_id: 'purpose-1',
  purpose_code: 'ACADEMIC',
  purpose_name: 'Tư vấn học tập',
  student_id: 'student-1',
  student_name: 'Sinh viên mẫu',
  duration_minutes: 45,
  starts_at: '2026-10-20T01:00:00Z',
  ends_at: '2026-10-20T01:45:00Z',
  location: null,
  status: 'UPCOMING',
  created_at: '2026-10-17T03:00:00Z',
  cancelled_at: null,
  outcome_at: null,
};

describe('staff API services', () => {
  it('rejects a staff mutation when the cookie belongs to another staff account', async () => {
    const fetcher = vi.fn<typeof fetch>(async (path) => {
      if (String(path).endsWith('/auth/csrf'))
        return json({ csrf_token: 'csrf', user_id: 'staff-b' });
      throw new Error(`Unexpected request: ${path}`);
    });
    const services = new ApiServices(fetcher);

    await expect(
      services.recordStaffOutcome('appointment-1', 'COMPLETED', { expectedUserId: 'staff-a' }),
    ).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' } satisfies Partial<ApiError>);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('rejects a staff mutation if the verified account has another role', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ csrf_token: 'csrf', user_id: staffUser.id }))
      .mockResolvedValueOnce(json({ ...staffUser, role: 'STUDENT' }));
    const services = new ApiServices(fetcher);

    await expect(services.setStaffPurpose('purpose-1', false, staffOptions)).rejects.toMatchObject({
      code: 'ACCOUNT_CHANGED',
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('maps the STAFF business-time context without using the student booking context', async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      json({ timezone: 'Asia/Ho_Chi_Minh', business_date: '2026-10-20' }),
    );
    const services = new ApiServices(fetcher);

    await expect(services.staffContext()).resolves.toEqual({
      timezone: 'Asia/Ho_Chi_Minh',
      businessDate: '2026-10-20',
    });
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/staff/me/context');
  });

  it('maps paginated assigned appointment filters and excludes free text from summaries', async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      json({ items: [staffAppointment], page: 2, page_size: 10, total: 11 }),
    );
    const services = new ApiServices(fetcher);

    const page = await services.staffAppointments({
      page: 2,
      pageSize: 10,
      status: 'UPCOMING',
      date: '2026-10-20',
    });

    expect(fetcher.mock.calls[0][0]).toBe(
      '/api/v1/staff/me/appointments?page=2&page_size=10&status=UPCOMING&date=2026-10-20',
    );
    expect(page).toMatchObject({
      page: 2,
      pageSize: 10,
      total: 11,
      items: [
        {
          id: 'appointment-1',
          studentName: 'Sinh viên mẫu',
          durationMinutes: 45,
          location: null,
          status: 'UPCOMING',
        },
      ],
    });
    expect(page.items[0]).not.toHaveProperty('description');
  });

  it('maps authorized appointment detail and preserves nullable historical fields', async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      json({
        ...staffAppointment,
        description: 'Trao đổi riêng',
        cancelled_by: null,
        cancel_reason: null,
        outcome_by: null,
      }),
    );

    await expect(new ApiServices(fetcher).staffAppointment('a/b')).resolves.toMatchObject({
      id: 'appointment-1',
      description: 'Trao đổi riêng',
      cancelReason: null,
      outcomeBy: null,
    });
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/staff/me/appointments/a%2Fb');
  });

  it('sends cancellation and outcome through same-origin CSRF-protected staff endpoints', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ csrf_token: 'csrf', user_id: staffUser.id }))
      .mockResolvedValueOnce(json(staffUser))
      .mockResolvedValueOnce(json({ id: 'a', status: 'CANCELLED', cancelled_at: 'now' }))
      .mockResolvedValueOnce(json({ csrf_token: 'csrf-2', user_id: staffUser.id }))
      .mockResolvedValueOnce(json(staffUser))
      .mockResolvedValueOnce(json({ id: 'a', status: 'COMPLETED', outcome_at: 'later' }));
    const services = new ApiServices(fetcher);

    await services.cancelStaffAppointment('a', 'requested', staffOptions);
    await services.recordStaffOutcome('a', 'COMPLETED', staffOptions);

    expect(fetcher.mock.calls[2]).toEqual([
      '/api/v1/staff/me/appointments/a/cancel',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf' }),
        body: JSON.stringify({ reason: 'requested' }),
      }),
    ]);
    expect(fetcher.mock.calls[5]).toEqual([
      '/api/v1/staff/me/appointments/a/outcome',
      expect.objectContaining({ body: JSON.stringify({ status: 'COMPLETED' }) }),
    ]);
  });

  it('maps purpose catalog and sends only the acceptance flag on update', async () => {
    const purpose = {
      id: 'purpose-1',
      code: 'ACADEMIC',
      name: 'Tư vấn học tập',
      duration_minutes: 45,
      requires_free_text: false,
      is_active: false,
      is_accepting: true,
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ items: [purpose], page: 1, page_size: 20, total: 1 }))
      .mockResolvedValueOnce(json({ csrf_token: 'csrf', user_id: staffUser.id }))
      .mockResolvedValueOnce(json(staffUser))
      .mockResolvedValueOnce(json({ ...purpose, is_accepting: false }));
    const services = new ApiServices(fetcher);

    await expect(services.staffPurposes()).resolves.toMatchObject({
      total: 1,
      items: [{ isActive: false, isAccepting: true, durationMinutes: 45 }],
    });
    await services.setStaffPurpose('purpose-1', false, staffOptions);

    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/staff/me/purposes?page=1&page_size=20');
    expect(fetcher.mock.calls[3]).toEqual([
      '/api/v1/staff/me/purposes/purpose-1',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ is_accepting: false }) }),
    ]);
  });

  it('maps unavailability filters, creates an offset-bearing interval, and deletes without a body', async () => {
    const period = {
      id: 'period-1',
      starts_at: '2026-10-20T01:00:00Z',
      ends_at: '2026-10-20T02:00:00Z',
      created_at: '2026-10-17T03:00:00Z',
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ items: [period], page: 1, page_size: 20, total: 1 }))
      .mockResolvedValueOnce(json({ csrf_token: 'csrf', user_id: staffUser.id }))
      .mockResolvedValueOnce(json(staffUser))
      .mockResolvedValueOnce(json(period, 201))
      .mockResolvedValueOnce(json({ csrf_token: 'csrf-2', user_id: staffUser.id }))
      .mockResolvedValueOnce(json(staffUser))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const services = new ApiServices(fetcher);

    await expect(
      services.staffUnavailability({ from: '2026-10-20T00:00:00+07:00', page: 1 }),
    ).resolves.toMatchObject({ items: [{ id: 'period-1' }] });
    await services.createStaffUnavailability(
      '2026-10-20T08:00:00+07:00',
      '2026-10-20T09:00:00+07:00',
      staffOptions,
    );
    await services.deleteStaffUnavailability('period-1', staffOptions);

    expect(fetcher.mock.calls[0][0]).toBe(
      '/api/v1/staff/me/unavailability?from=2026-10-20T00%3A00%3A00%2B07%3A00&page=1&page_size=20',
    );
    expect(fetcher.mock.calls[3]).toEqual([
      '/api/v1/staff/me/unavailability',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          starts_at: '2026-10-20T08:00:00+07:00',
          ends_at: '2026-10-20T09:00:00+07:00',
        }),
      }),
    ]);
    expect(fetcher.mock.calls[6]).toEqual([
      '/api/v1/staff/me/unavailability/period-1',
      expect.objectContaining({ method: 'DELETE' }),
    ]);
    expect(fetcher.mock.calls[6][1]).not.toHaveProperty('body');
  });
});
