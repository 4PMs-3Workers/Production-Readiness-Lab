// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';

const user = {
  id: 'student-a',
  name: 'Sinh viên A',
  email: 'student@example.invalid',
  role: 'STUDENT',
};
const notification = {
  id: 'notification-a',
  event_type: 'APPOINTMENT_CREATED',
  summary: 'Lịch hẹn đã được tạo',
  created_at: '2026-10-22T03:00:00Z',
  read_at: null,
  appointment_id: 'appointment-a',
  appointment_starts_at: '2026-10-23T03:00:00Z',
  appointment_status: 'UPCOMING',
};
const json = (value: unknown, status = 200, headers?: HeadersInit) =>
  Response.json(value, { status, headers });

describe('notification API services', () => {
  it('serializes paging and unread filter, and maps the wire contract', async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      json({ items: [notification], page: 2, page_size: 20, total: 21 }),
    );
    const page = await new ApiServices(fetcher).notificationPage({
      page: 2,
      pageSize: 20,
      unreadOnly: true,
    });

    expect(fetcher.mock.calls[0][0]).toBe(
      '/api/v1/notifications?page=2&page_size=20&unread_only=true',
    );
    expect(fetcher.mock.calls[0][1]).toMatchObject({ cache: 'no-store' });
    expect(page).toMatchObject({
      page: 2,
      pageSize: 20,
      total: 21,
      items: [
        {
          id: 'notification-a',
          eventType: 'APPOINTMENT_CREATED',
          summary: 'Lịch hẹn đã được tạo',
          readAt: null,
          appointmentId: 'appointment-a',
        },
      ],
    });
  });

  it('uses the authenticated identity, CSRF token, empty body, and read endpoint', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(user))
      .mockResolvedValueOnce(json({ csrf_token: 'signed-token', user_id: user.id }))
      .mockResolvedValueOnce(json(user))
      .mockResolvedValueOnce(json({ id: 'notification-a', read_at: '2026-10-22T03:04:05Z' }));
    const result = await new ApiServices(fetcher).markNotificationRead('notification-a');

    expect(result).toEqual({ id: 'notification-a', readAt: '2026-10-22T03:04:05Z' });
    expect(fetcher.mock.calls[3][0]).toBe('/api/v1/notifications/notification-a/read');
    expect(fetcher.mock.calls[3][1]).toMatchObject({
      method: 'POST',
      body: '{}',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'signed-token' }),
    });
  });

  it('preserves server error code and request ID for recoverable failures', async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      json({ error: { code: 'SERVICE_UNAVAILABLE' } }, 503, { 'X-Request-ID': 'request-a' }),
    );

    await expect(
      new ApiServices(fetcher).notificationPage({ page: 1, pageSize: 20, unreadOnly: false }),
    ).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE', status: 503, requestId: 'request-a' });
  });
});
