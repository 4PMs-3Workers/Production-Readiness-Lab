// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { ApiError, errorMessage } from '@/services/api/errors';
import { BookingIntentStore } from '@/services/api/booking-intent';

const wireUser = {
  id: 'student-a',
  name: 'Student A',
  email: 'a@example.invalid',
  role: 'STUDENT',
};
const body = {
  purpose_id: 'purpose-a',
  staff_id: 'staff-a',
  starts_at: '2026-10-20T01:00:00Z',
  description: '  fictional text  ',
};
const receipt = {
  id: 'appointment-a',
  status: 'UPCOMING',
  created_at: '2026-10-17T03:00:00.123456Z',
};
const json = (value: unknown, status = 200) => Response.json(value, { status });
afterEach(() => vi.useRealTimers());

describe('real HTTP services', () => {
  it('uses relative same-origin no-store JSON requests and maps identity', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => json(wireUser));
    const services = new ApiServices(fetcher);
    expect(await services.login({ email: 'a@example.invalid', password: 'fictional' })).toEqual({
      id: 'student-a',
      displayName: 'Student A',
      email: 'a@example.invalid',
      role: 'STUDENT',
    });
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/auth/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        signal: expect.any(AbortSignal),
        body: JSON.stringify({ email: 'a@example.invalid', password: 'fictional' }),
      }),
    );
  });
  it('preserves catalog/staff pagination and arbitrary purpose codes', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        json({
          items: [
            {
              id: 'p',
              code: 'NEW_BACKEND_CODE',
              name: 'Purpose',
              duration_minutes: 45,
              requires_free_text: true,
            },
          ],
          page: 2,
          page_size: 20,
          total: 25,
        }),
      )
      .mockResolvedValueOnce(
        json({ items: [{ id: 's', name: 'Advisor' }], page: 2, page_size: 20, total: 21 }),
      );
    const services = new ApiServices(fetcher);
    const purposes = await services.purposes(2);
    expect(purposes).toMatchObject({
      page: 2,
      pageSize: 20,
      total: 25,
      items: [{ code: 'NEW_BACKEND_CODE', durationMinutes: 45, requiresFreeText: true }],
    });
    expect(await services.staff('p', 2)).toMatchObject({
      page: 2,
      total: 21,
      items: [{ displayName: 'Advisor' }],
    });
    expect(fetcher.mock.calls[1][0]).toBe('/api/v1/staff?purpose_id=p&page=2&page_size=20');
  });
  it('uses server booking context and exact availability timestamps without inventing a location', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        json({
          timezone: 'Pacific/Auckland',
          business_date: '2026-10-17',
          min_booking_date: '2026-10-18',
          max_booking_date: '2026-11-16',
        }),
      )
      .mockResolvedValueOnce(
        json({
          date: '2026-10-18',
          timezone: 'Pacific/Auckland',
          slots: [
            { starts_at: '2026-10-17T19:00:00.123456Z', ends_at: '2026-10-17T19:45:00.123456Z' },
          ],
        }),
      );
    const services = new ApiServices(fetcher);
    expect(await services.context()).toMatchObject({
      timezone: 'Pacific/Auckland',
      minBookingDate: '2026-10-18',
      maxBookingDate: '2026-11-16',
    });
    expect(
      await services.slots({ purposeId: 'p', staffId: 's', date: '2026-10-18' }),
    ).toMatchObject([
      {
        startsAt: '2026-10-17T19:00:00.123456Z',
        endsAt: '2026-10-17T19:45:00.123456Z',
        location: null,
        purposeId: 'p',
        staffId: 's',
      },
    ]);
  });
  it('bootstraps CSRF and sends only the reviewed creation body/key', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ csrf_token: 'test-csrf' }))
      .mockResolvedValueOnce(json(receipt, 201));
    const services = new ApiServices(fetcher);
    expect(await services.create(body, 'key-a')).toEqual(receipt);
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/auth/csrf');
    expect(fetcher.mock.calls[1]).toEqual([
      '/api/v1/appointments',
      expect.objectContaining({
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRF-Token': 'test-csrf',
          'Idempotency-Key': 'key-a',
        },
        body: JSON.stringify(body),
      }),
    ]);
  });
  it.each([
    'INVALID_CREDENTIALS',
    'LOGIN_THROTTLED',
    'INVALID_SESSION',
    'FORBIDDEN_ROLE',
    'INVALID_CSRF',
    'INVALID_ORIGIN',
    'BOOKING_CONFLICT',
    'BOOKING_QUOTA_EXCEEDED',
    'IDEMPOTENCY_CONFLICT',
    'CANCELLATION_DEADLINE_PASSED',
    'APPOINTMENT_STATE_CONFLICT',
    'VALIDATION_ERROR',
    'SERVICE_UNAVAILABLE',
  ])('keeps %s distinct while hiding raw error bodies', async (code) => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      Response.json(
        { error: { code, message: 'SQL secret consultation stacktrace' } },
        {
          status: code === 'SERVICE_UNAVAILABLE' ? 503 : 409,
          headers: { 'X-Request-ID': 'support-123', 'Retry-After': '30' },
        },
      ),
    );
    const services = new ApiServices(fetcher);
    const error = await services.me().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code, requestId: 'support-123', retryAfter: '30' });
    expect(errorMessage(error)).not.toContain('SQL secret');
  });
  it('distinguishes a caller abort from a bounded request timeout', async () => {
    const fetcher = vi.fn<typeof fetch>(
      (_url, options) =>
        new Promise((_resolve, reject) =>
          options!.signal!.addEventListener('abort', () =>
            reject(new DOMException('Cancelled', 'AbortError')),
          ),
        ),
    );
    const services = new ApiServices(fetcher, 50);
    const controller = new AbortController();
    const cancelled = services.me({ signal: controller.signal });
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
    vi.useFakeTimers();
    const timedOut = expect(services.me()).rejects.toMatchObject({ code: 'TIMEOUT' });
    await vi.advanceTimersByTimeAsync(51);
    await timedOut;
  });
  it('does not submit an already cancelled request', async () => {
    const fetcher = vi.fn<typeof fetch>();
    const controller = new AbortController();
    controller.abort();
    await expect(new ApiServices(fetcher).me({ signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('sends a strict cancellation body and refreshes CSRF for logout', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({ csrf_token: 'csrf-1' }))
      .mockResolvedValueOnce(
        json({ id: 'a', status: 'CANCELLED', cancelled_at: '2026-10-17T03:00:00Z' }),
      )
      .mockResolvedValueOnce(json({ csrf_token: 'csrf-2' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const services = new ApiServices(fetcher);
    await services.cancel('a', undefined);
    await services.logout();
    expect(fetcher.mock.calls[1]).toEqual([
      '/api/v1/appointments/a/cancel',
      expect.objectContaining({ body: '{}' }),
    ]);
    expect(fetcher.mock.calls[3]).toEqual([
      '/api/v1/auth/logout',
      expect.objectContaining({ headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-2' }) }),
    ]);
  });
});

describe('booking intent recovery', () => {
  function servicesWith(create: () => Promise<Response>, identity = wireUser) {
    return new ApiServices(
      vi.fn<typeof fetch>(async (path) =>
        String(path).endsWith('/me')
          ? json(identity)
          : String(path).endsWith('/csrf')
            ? json({ csrf_token: 'csrf' })
            : create(),
      ),
    );
  }
  it('retries a lost response with the same immutable body and key, then retains the receipt', async () => {
    const posted: RequestInit[] = [];
    let attempts = 0;
    const services = new ApiServices(
      vi.fn<typeof fetch>(async (path, init) => {
        if (String(path).endsWith('/me')) return json(wireUser);
        if (String(path).endsWith('/csrf')) return json({ csrf_token: 'csrf' });
        posted.push(init!);
        if (++attempts === 1) throw new TypeError('Connection lost');
        return json(receipt);
      }),
    );
    const store = new BookingIntentStore();
    const prepared = store.prepare(wireUser.id, body);
    expect(Object.isFrozen(prepared.body)).toBe(true);
    await expect(store.submit(services, wireUser.id)).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
    });
    expect(store.snapshot()?.phase).toBe('unknown');
    expect(store.prepare(wireUser.id, { ...body, description: 'different' })).toMatchObject({
      key: prepared.key,
      body,
    });
    expect(await store.submit(services, wireUser.id)).toEqual(receipt);
    expect(posted[0].body).toBe(posted[1].body);
    expect(posted[0].headers).toEqual(posted[1].headers);
    expect(JSON.parse(String(posted[1].body)).description).toBe('  fictional text  ');
    expect(await store.submit(services, wireUser.id)).toEqual(receipt);
    expect(posted).toHaveLength(2);
  });
  it('deduplicates simultaneous submissions', async () => {
    const create = vi.fn(async () => json(receipt));
    const store = new BookingIntentStore();
    store.prepare(wireUser.id, body);
    const services = servicesWith(create);
    const first = store.submit(services, wireUser.id);
    expect(store.submit(services, wireUser.id)).toBe(first);
    await first;
    expect(create).toHaveBeenCalledTimes(1);
  });
  it('clears the body and never posts after a verified account switch', async () => {
    const create = vi.fn(async () => json(receipt));
    const store = new BookingIntentStore();
    store.prepare(wireUser.id, body);
    await expect(
      store.submit(servicesWith(create, { ...wireUser, id: 'other' }), wireUser.id),
    ).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' });
    expect(store.snapshot()).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });
  it.each([
    ['BOOKING_CONFLICT', null],
    ['BOOKING_QUOTA_EXCEEDED', null],
    ['IDEMPOTENCY_CONFLICT', 'conflict'],
    ['SERVICE_UNAVAILABLE', 'unknown'],
  ] as const)('preserves the correct recovery state for %s', async (code, phase) => {
    const store = new BookingIntentStore();
    store.prepare(wireUser.id, body);
    const services = servicesWith(async () =>
      json({ error: { code } }, code === 'SERVICE_UNAVAILABLE' ? 503 : 409),
    );
    await expect(store.submit(services, wireUser.id)).rejects.toMatchObject({ code });
    expect(store.snapshot()?.phase ?? null).toBe(phase);
    if (phase === 'conflict') {
      store.startNew();
      expect(store.snapshot()?.phase).toBe('conflict');
    }
  });
});
