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
      .mockResolvedValueOnce(json({ csrf_token: 'test-csrf', user_id: wireUser.id }))
      .mockResolvedValueOnce(json(wireUser))
      .mockResolvedValueOnce(json(receipt, 201));
    const services = new ApiServices(fetcher);
    expect(await services.create(body, 'key-a', wireUser.id)).toEqual(receipt);
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/auth/csrf');
    expect(fetcher.mock.calls[1][0]).toBe('/api/v1/auth/me');
    expect(fetcher.mock.calls[2]).toEqual([
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
            ? json({ csrf_token: 'csrf', user_id: identity.id })
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
        if (String(path).endsWith('/csrf'))
          return json({ csrf_token: 'csrf', user_id: wireUser.id });
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
    ['student-b', wireUser],
    [wireUser.id, { ...wireUser, id: 'student-b' }],
    [wireUser.id, { ...wireUser, role: 'STAFF' }],
  ])('rejects a different CSRF owner or post-bootstrap identity', async (owner, identity) => {
    const fetcher = vi.fn<typeof fetch>(async (path) => {
      if (String(path).endsWith('/csrf')) return json({ csrf_token: 'csrf', user_id: owner });
      if (String(path).endsWith('/me')) return json(identity);
      return json(receipt);
    });
    const store = new BookingIntentStore();
    const services = new ApiServices(fetcher);
    store.prepare(wireUser.id, body);
    await expect(store.submit(services, wireUser.id)).rejects.toMatchObject({
      code: 'ACCOUNT_CHANGED',
    });
    expect(store.snapshot()).toBeNull();
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/appointments'))).toBe(false);
  });
  it('fails closed when the CSRF bootstrap does not identify its owner', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => json({ csrf_token: 'csrf' }));
    await expect(new ApiServices(fetcher).create(body, 'key', wireUser.id)).rejects.toMatchObject({
      code: 'SERVICE_UNAVAILABLE',
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('keeps the original signed token when the cookie changes after identity verification', async () => {
    let activeUser = wireUser.id;
    let written = false;
    const fetcher = vi.fn<typeof fetch>(async (path, init) => {
      if (String(path).endsWith('/csrf'))
        return json({ csrf_token: `csrf:${activeUser}`, user_id: activeUser });
      if (String(path).endsWith('/me')) {
        activeUser = 'student-b';
        return json(wireUser);
      }
      const token = new Headers(init?.headers).get('X-CSRF-Token');
      expect(token).toBe(`csrf:${wireUser.id}`);
      if (token !== `csrf:${activeUser}`) return json({ error: { code: 'INVALID_CSRF' } }, 403);
      written = true;
      return json(receipt);
    });
    const store = new BookingIntentStore();
    store.prepare(wireUser.id, body);
    await expect(store.submit(new ApiServices(fetcher), wireUser.id)).rejects.toMatchObject({
      code: 'INVALID_CSRF',
    });
    expect(written).toBe(false);
  });
  it.each(['/csrf', '/me'])(
    'does not POST after clearing an intent while awaiting %s',
    async (held) => {
      let finish!: (response: Response) => void;
      const fetcher = vi.fn<typeof fetch>(async (path) => {
        if (String(path).endsWith(held))
          return new Promise((resolve) => {
            finish = resolve;
          }); // Deliberately ignores AbortSignal to exercise the post-response guard.
        return json({ csrf_token: 'csrf', user_id: wireUser.id });
      });
      const store = new BookingIntentStore();
      store.prepare(wireUser.id, body);
      const pending = store.submit(new ApiServices(fetcher), wireUser.id);
      await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
      store.clear();
      const replacement = store.prepare(wireUser.id, { ...body, description: 'replacement' });
      finish(json(held === '/me' ? wireUser : { csrf_token: 'csrf', user_id: wireUser.id }));
      await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
      expect(store.snapshot()).toBe(replacement);
      expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/appointments'))).toBe(
        false,
      );
    },
  );
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
