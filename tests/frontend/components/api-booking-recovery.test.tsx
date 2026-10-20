import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiBookingFlow } from '@/features/api/api-booking-flow';
import { ApiProvider, useApi } from '@/features/api/api-provider';
import { SessionGate } from '@/features/api/session-gate';
import { ApiServices } from '@/services/api/client';

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/booking',
  useSearchParams: () => new URLSearchParams(''),
}));
const user = {
  id: 'student',
  name: 'Fictional Student',
  email: 'a@example.invalid',
  role: 'STUDENT',
};
const context = {
  timezone: 'Asia/Ho_Chi_Minh',
  business_date: '2026-10-18',
  min_booking_date: '2026-10-19',
  max_booking_date: '2026-10-25',
};
const body = { purpose_id: 'p', staff_id: 's', starts_at: '2026-10-19T01:00:00Z' };
const receipt = { id: 'receipt', status: 'UPCOMING', created_at: '2026-10-18T01:00:00Z' };
const json = (data: unknown, status = 200) => Response.json(data, { status });
beforeEach(() => {
  router.replace.mockClear();
  router.push.mockClear();
});

function setup(fetcher: typeof fetch) {
  const services = new ApiServices(fetcher);
  let api!: ReturnType<typeof useApi>;
  function Capture() {
    const value = useApi();
    useEffect(() => {
      api = value;
    }, [value]);
    return null;
  }
  const flow = (booking: boolean) => (
    <ApiProvider services={services}>
      <Capture />
      <SessionGate>{booking ? <ApiBookingFlow /> : <p>Trang chủ</p>}</SessionGate>
    </ApiProvider>
  );
  const view = render(flow(true));
  return {
    services,
    api: () => api,
    showBooking: (booking: boolean) => view.rerender(flow(booking)),
  };
}

describe('booking recovery boundaries', () => {
  it('clears a same-user draft and context cache after an authenticated session revision', async () => {
    let contextReads = 0;
    const fixture = setup(
      vi.fn<typeof fetch>(async (path) => {
        const url = String(path);
        if (url.endsWith('/auth/me') || url.endsWith('/auth/login')) return json(user);
        if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf', user_id: user.id });
        if (url.endsWith('/booking-context')) {
          contextReads += 1;
          return json(context);
        }
        if (url.includes('/purposes?'))
          return json({
            items: [
              {
                id: 'p',
                code: 'CUSTOM',
                name: 'Purpose',
                duration_minutes: 30,
                requires_free_text: true,
              },
            ],
            page: 1,
            page_size: 20,
            total: 1,
          });
        return json({ items: [], page: 1, page_size: 20, total: 0 });
      }),
    );
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
    await controls.type(
      screen.getByLabelText('Bạn muốn trao đổi về điều gì? *'),
      'Old session draft',
    );
    expect(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *')).toHaveValue(
      'Old session draft',
    );

    await act(async () => fixture.api().login({ email: user.email, password: 'fictional' }));

    await waitFor(() => expect(contextReads).toBe(2));
    expect(screen.getByRole('radio', { name: /Purpose/ })).not.toBeChecked();
    expect(screen.queryByLabelText('Bạn muốn trao đổi về điều gì? *')).not.toBeInTheDocument();
  });

  it('replays an unknown intent while booking context is unavailable', async () => {
    let contextReads = 0;
    const posted: RequestInit[] = [];
    const fixture = setup(
      vi.fn<typeof fetch>(async (path, init) => {
        const url = String(path);
        if (url.endsWith('/auth/me')) return json(user);
        if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf', user_id: user.id });
        if (url.endsWith('/booking-context')) {
          contextReads += 1;
          return json({ error: { code: 'SERVICE_UNAVAILABLE' } }, 503);
        }
        if (init?.method === 'POST') {
          posted.push(init);
          if (posted.length === 1) throw new TypeError('Response was lost');
          return json(receipt);
        }
        return json({ items: [], page: 1, page_size: 20, total: 0 });
      }),
    );
    await screen.findByText(/Dịch vụ tạm thời chưa sẵn sàng/);
    let key!: string;
    let firstAttempt!: Promise<unknown>;
    act(() => {
      key = fixture.api().intents.prepare(user.id, body).key;
      firstAttempt = fixture
        .api()
        .intents.submit(fixture.services, user.id)
        .catch((error: unknown) => error);
    });
    await act(async () => firstAttempt);

    await userEvent.click(await screen.findByRole('button', { name: 'Thử lại cùng yêu cầu' }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/booking/result?id=receipt'));
    expect(contextReads).toBe(1);
    expect(posted).toHaveLength(2);
    expect(posted[0].body).toBe(JSON.stringify(body));
    expect(posted[1].body).toBe(posted[0].body);
    for (const request of posted)
      expect(new Headers(request.headers).get('Idempotency-Key')).toBe(key);
  });

  it('retries failed availability without refreshing context or losing the draft and step', async () => {
    let contextReads = 0;
    let availabilityReads = 0;
    setup(
      vi.fn<typeof fetch>(async (path) => {
        const url = String(path);
        if (url.endsWith('/auth/me')) return json(user);
        if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf', user_id: user.id });
        if (url.endsWith('/booking-context')) {
          contextReads += 1;
          return json(context);
        }
        if (url.includes('/purposes?'))
          return json({
            items: [
              {
                id: 'p',
                code: 'CUSTOM',
                name: 'Purpose',
                duration_minutes: 30,
                requires_free_text: true,
              },
            ],
            page: 1,
            page_size: 20,
            total: 1,
          });
        if (url.includes('/staff?'))
          return json({
            items: [{ id: 's', name: 'Advisor' }],
            page: 1,
            page_size: 20,
            total: 1,
          });
        if (url.includes('/availability?')) {
          availabilityReads += 1;
          return availabilityReads === 1
            ? json({ error: { code: 'SERVICE_UNAVAILABLE' } }, 503)
            : json({
                date: '2026-10-19',
                timezone: context.timezone,
                slots: [
                  {
                    starts_at: body.starts_at,
                    ends_at: '2026-10-19T01:30:00Z',
                  },
                ],
              });
        }
        return json({ items: [], page: 1, page_size: 20, total: 0 });
      }),
    );
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
    await controls.type(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *'), 'Draft detail');
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(await screen.findByRole('radio', { name: /Advisor/ }));
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(await screen.findByRole('radio', { name: '19/10/2026' }));
    await screen.findByText(/Dịch vụ tạm thời chưa sẵn sàng/);

    await controls.click(screen.getByRole('button', { name: 'Thử lại' }));

    await screen.findByRole('radio', { name: /08:00 – 08:30/ });
    expect(contextReads).toBe(1);
    expect(availabilityReads).toBe(2);
    expect(screen.getByText('BƯỚC 03')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '19/10/2026' })).toBeChecked();
    await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
    await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
    expect(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *')).toHaveValue('Draft detail');
  });

  it.each([
    {
      scenario: 'business midnight',
      rejectedDate: '19/10/2026',
      nextContext: {
        ...context,
        business_date: '2026-10-19',
        min_booking_date: '2026-10-20',
        max_booking_date: '2026-10-26',
      },
      availableDate: '26/10/2026',
    },
    {
      scenario: 'a shorter booking horizon',
      rejectedDate: '25/10/2026',
      nextContext: { ...context, max_booking_date: '2026-10-21' },
      availableDate: '21/10/2026',
    },
    {
      scenario: 'unchanged context',
      rejectedDate: '19/10/2026',
      nextContext: context,
      availableDate: '19/10/2026',
    },
  ])(
    'recovers availability validation errors after $scenario without losing the draft',
    async ({ rejectedDate, nextContext, availableDate }) => {
      let contextReads = 0;
      let availabilityReads = 0;
      setup(
        vi.fn<typeof fetch>(async (path) => {
          const url = String(path);
          if (url.endsWith('/auth/me')) return json(user);
          if (url.endsWith('/booking-context')) {
            contextReads += 1;
            return json(contextReads === 1 ? context : nextContext);
          }
          if (url.includes('/purposes?'))
            return json({
              items: [
                {
                  id: 'p',
                  code: 'CUSTOM',
                  name: 'Purpose',
                  duration_minutes: 30,
                  requires_free_text: true,
                },
              ],
              page: 1,
              page_size: 20,
              total: 1,
            });
          if (url.includes('/staff?'))
            return json({
              items: [{ id: 's', name: 'Advisor' }],
              page: 1,
              page_size: 20,
              total: 1,
            });
          if (url.includes('/availability?')) {
            availabilityReads += 1;
            const date = new URL(url, 'http://localhost').searchParams.get('date')!;
            if (
              availabilityReads === 1 ||
              date < nextContext.min_booking_date ||
              date > nextContext.max_booking_date
            )
              return json({ error: { code: 'VALIDATION_ERROR' } }, 422);
            return json({
              date,
              timezone: nextContext.timezone,
              slots: [{ starts_at: `${date}T01:00:00Z`, ends_at: `${date}T01:30:00Z` }],
            });
          }
          throw new Error(`Unexpected request: ${url}`);
        }),
      );
      const controls = userEvent.setup();
      await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
      await controls.type(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *'), 'Draft detail');
      await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
      await controls.click(await screen.findByRole('radio', { name: /Advisor/ }));
      await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
      await controls.click(screen.getByRole('radio', { name: rejectedDate }));
      await screen.findByText(/Thông tin chưa hợp lệ hoặc cửa sổ ngày đặt đã thay đổi/);

      await controls.click(screen.getByRole('button', { name: 'Thử lại' }));

      await waitFor(() => expect(contextReads).toBe(2));
      expect(screen.getByText('BƯỚC 03')).toBeInTheDocument();
      if (rejectedDate !== availableDate) {
        await waitFor(() =>
          expect(screen.queryByRole('radio', { name: rejectedDate })).not.toBeInTheDocument(),
        );
        expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
        await controls.click(screen.getByRole('radio', { name: availableDate }));
      } else {
        expect(screen.getByRole('radio', { name: rejectedDate })).toBeChecked();
      }
      await controls.click(await screen.findByRole('radio', { name: /08:00 – 08:30/ }));
      expect(availabilityReads).toBeGreaterThan(1);
      expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled();
      await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
      expect(await screen.findByRole('radio', { name: /Advisor/ })).toBeChecked();
      await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
      expect(await screen.findByRole('radio', { name: /Purpose/ })).toBeChecked();
      expect(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *')).toHaveValue('Draft detail');
    },
  );

  it('keeps the review step and draft mounted during a same-context 422 refresh', async () => {
    const refreshedContext = Promise.withResolvers<Response>();
    let contextReads = 0;
    const fixture = setup(
      vi.fn<typeof fetch>(async (path, init) => {
        const url = String(path);
        if (url.endsWith('/auth/me')) return json(user);
        if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf', user_id: user.id });
        if (url.endsWith('/booking-context')) {
          contextReads += 1;
          return contextReads === 1 ? json(context) : refreshedContext.promise;
        }
        if (url.includes('/purposes?'))
          return json({
            items: [
              {
                id: 'p',
                code: 'CUSTOM',
                name: 'Purpose',
                duration_minutes: 30,
                requires_free_text: true,
              },
            ],
            page: 1,
            page_size: 20,
            total: 1,
          });
        if (url.includes('/staff?'))
          return json({ items: [{ id: 's', name: 'Advisor' }], page: 1, page_size: 20, total: 1 });
        if (url.includes('/availability?'))
          return json({
            date: '2026-10-19',
            timezone: context.timezone,
            slots: [{ starts_at: body.starts_at, ends_at: '2026-10-19T01:30:00Z' }],
          });
        if (init?.method === 'POST') return json({ error: { code: 'VALIDATION_ERROR' } }, 422);
        return json({ items: [], page: 1, page_size: 20, total: 0 });
      }),
    );
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
    await controls.type(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *'), 'Draft detail');
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(await screen.findByRole('radio', { name: /Advisor/ }));
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(await screen.findByRole('radio', { name: '19/10/2026' }));
    await controls.click(await screen.findByRole('radio', { name: /08:00 – 08:30/ }));
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(screen.getByRole('button', { name: /Xác nhận đặt lịch/ }));

    await waitFor(() => expect(contextReads).toBe(2));
    expect(screen.getByText('BƯỚC 04')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Xác nhận đặt lịch/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '← Quay lại' })).toBeEnabled();
    await act(async () => refreshedContext.resolve(json(context)));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Xác nhận đặt lịch/ })).toBeEnabled(),
    );
    expect(screen.getByText('BƯỚC 04')).toBeInTheDocument();
    await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
    await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
    await controls.click(screen.getByRole('button', { name: '← Quay lại' }));
    expect(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *')).toHaveValue('Draft detail');
    expect(fixture.api().intents.snapshot()).toBeNull();
  });

  it('reloads availability after timezone changes while retaining an in-range date', async () => {
    let contextReads = 0;
    let availabilityReads = 0;
    const fixture = setup(
      vi.fn<typeof fetch>(async (path, init) => {
        const url = String(path);
        if (url.endsWith('/auth/me')) return json(user);
        if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf', user_id: user.id });
        if (url.endsWith('/booking-context')) {
          contextReads += 1;
          return json(contextReads === 1 ? context : { ...context, timezone: 'UTC' });
        }
        if (url.includes('/purposes?'))
          return json({
            items: [
              {
                id: 'p',
                code: 'CUSTOM',
                name: 'Purpose',
                duration_minutes: 30,
                requires_free_text: true,
              },
            ],
            page: 1,
            page_size: 20,
            total: 1,
          });
        if (url.includes('/staff?'))
          return json({ items: [{ id: 's', name: 'Advisor' }], page: 1, page_size: 20, total: 1 });
        if (url.includes('/availability?')) {
          availabilityReads += 1;
          const startsAt = availabilityReads === 1 ? body.starts_at : '2026-10-19T02:00:00Z';
          return json({
            date: '2026-10-19',
            timezone: availabilityReads === 1 ? context.timezone : 'UTC',
            slots: [
              {
                starts_at: startsAt,
                ends_at: new Date(Date.parse(startsAt) + 1800000).toISOString(),
              },
            ],
          });
        }
        if (init?.method === 'POST') return json({ error: { code: 'VALIDATION_ERROR' } }, 422);
        return json({ items: [], page: 1, page_size: 20, total: 0 });
      }),
    );
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
    await controls.type(screen.getByLabelText('Bạn muốn trao đổi về điều gì? *'), 'Draft detail');
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(await screen.findByRole('radio', { name: /Advisor/ }));
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(await screen.findByRole('radio', { name: '19/10/2026' }));
    await controls.click(await screen.findByRole('radio', { name: /08:00 – 08:30/ }));
    await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await controls.click(screen.getByRole('button', { name: /Xác nhận đặt lịch/ }));

    await screen.findByRole('radio', { name: /02:00 – 02:30/ });
    await waitFor(() => expect(availabilityReads).toBe(2));
    expect(contextReads).toBe(2);
    expect(screen.getByText('BƯỚC 03')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '19/10/2026' })).toBeChecked();
    expect(screen.queryByRole('radio', { name: /08:00 – 08:30/ })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /02:00 – 02:30/ })).not.toBeChecked();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    expect(fixture.api().intents.snapshot()).toBeNull();
  });

  it.each(['same-student', 'different-student', 'logout'])(
    'handles %s recovery after focus expiry while the first receipt is pending',
    async (recovery) => {
      let expired = false;
      let activeUser = user;
      let finish!: (response: Response) => void;
      const posted: RequestInit[] = [];
      const fixture = setup(
        vi.fn<typeof fetch>(async (path, init) => {
          if (String(path).endsWith('/auth/login')) {
            expired = false;
            return json(activeUser);
          }
          if (expired) return json({ error: { code: 'INVALID_SESSION' } }, 401);
          if (String(path).endsWith('/auth/me')) return json(user);
          if (String(path).endsWith('/auth/csrf'))
            return json({ csrf_token: 'csrf', user_id: user.id });
          if (String(path).endsWith('/booking-context')) return json(context);
          if (init?.method === 'POST') {
            posted.push(init);
            if (posted.length === 1)
              return new Promise((resolve) => {
                finish = resolve;
              });
            return json(receipt);
          }
          return json({ items: [], page: 1, page_size: 20, total: 0 });
        }),
      );
      await screen.findByRole('heading', { name: 'Đặt lịch tư vấn' });
      let pending!: Promise<unknown>;
      let key!: string;
      act(() => {
        key = fixture.api().intents.prepare(user.id, body).key;
        pending = fixture
          .api()
          .intents.submit(fixture.services, user.id)
          .catch((error: unknown) => error);
      });
      await waitFor(() => expect(posted).toHaveLength(1));
      expired = true;
      act(() => {
        window.dispatchEvent(new Event('focus'));
      });
      await waitFor(() => expect(fixture.api().session.status).toBe('anonymous'));
      expect(screen.queryByRole('heading', { name: 'Đặt lịch tư vấn' })).not.toBeInTheDocument();
      expect(fixture.api().intents.snapshot()).toMatchObject({ key, body, phase: 'unknown' });
      if (recovery !== 'same-student') {
        if (recovery === 'logout') await act(async () => fixture.api().logout());
        else {
          activeUser = { ...user, id: 'other-student' };
          await act(async () =>
            fixture.api().login({ email: activeUser.email, password: 'fictional' }),
          );
        }
        expect(fixture.api().intents.snapshot()).toBeNull();
        await act(async () => {
          finish(json(receipt));
          await pending;
        });
        expect(fixture.api().intents.snapshot()).toBeNull();
        expect(posted).toHaveLength(1);
        return;
      }
      await act(async () => fixture.api().login({ email: user.email, password: 'fictional' }));
      await userEvent.click(await screen.findByRole('button', { name: 'Thử lại cùng yêu cầu' }));
      await waitFor(() => expect(router.push).toHaveBeenCalledWith('/booking/result?id=receipt'));
      expect(posted[1].body).toBe(posted[0].body);
      expect(new Headers(posted[1].headers).get('Idempotency-Key')).toBe(key);
      await act(async () => {
        finish(json(receipt));
        await pending;
      });
      expect(fixture.api().intents.snapshot()).toMatchObject({ key, phase: 'confirmed', receipt });
    },
  );

  it.each([false, true])(
    'unlocks the wizard after an unknown request receives BOOKING_CONFLICT (remount: %s)',
    async (remount) => {
      const posted: RequestInit[] = [];
      let availabilityReads = 0;
      const fixture = setup(
        vi.fn<typeof fetch>(async (path, init) => {
          const url = String(path);
          if (url.endsWith('/auth/me')) return json(user);
          if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf', user_id: user.id });
          if (url.endsWith('/booking-context')) return json(context);
          if (url.includes('/purposes?'))
            return json({
              items: [
                {
                  id: 'p',
                  code: 'CUSTOM',
                  name: 'Purpose',
                  duration_minutes: 30,
                  requires_free_text: false,
                },
              ],
              page: 1,
              page_size: 20,
              total: 1,
            });
          if (url.includes('/staff?'))
            return json({
              items: [{ id: 's', name: 'Advisor' }],
              page: 1,
              page_size: 20,
              total: 1,
            });
          if (url.includes('/availability?')) {
            availabilityReads += 1;
            return json({
              date: '2026-10-19',
              timezone: context.timezone,
              slots:
                availabilityReads === 1
                  ? [{ starts_at: body.starts_at, ends_at: '2026-10-19T01:30:00Z' }]
                  : [{ starts_at: '2026-10-19T01:30:00Z', ends_at: '2026-10-19T02:00:00Z' }],
            });
          }
          posted.push(init!);
          if (posted.length === 1) throw new TypeError('Request lost before reaching server');
          if (posted.length === 2) return json({ error: { code: 'BOOKING_CONFLICT' } }, 409);
          return json(receipt);
        }),
      );
      const controls = userEvent.setup();
      await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
      await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
      await controls.click(await screen.findByRole('radio', { name: /Advisor/ }));
      await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
      await controls.click(screen.getByRole('radio', { name: '19/10/2026' }));
      await controls.click(await screen.findByRole('radio', { name: /08:00 – 08:30/ }));
      await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
      await controls.click(screen.getByRole('button', { name: /Xác nhận đặt lịch/ }));
      const original = fixture.api().intents.snapshot()!;
      await screen.findByRole('button', { name: 'Thử lại cùng yêu cầu' });
      if (remount) {
        fixture.showBooking(false);
        expect(screen.queryByRole('heading', { name: 'Đặt lịch tư vấn' })).not.toBeInTheDocument();
        fixture.showBooking(true);
        expect(fixture.api().intents.snapshot()).toMatchObject({
          key: original.key,
          body: original.body,
          phase: 'unknown',
        });
      }
      await controls.click(await screen.findByRole('button', { name: 'Thử lại cùng yêu cầu' }));
      if (remount) {
        await controls.click(await screen.findByRole('radio', { name: /Purpose/ }));
        await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
        await controls.click(await screen.findByRole('radio', { name: /Advisor/ }));
        // The restored wizard also permits normal backward navigation.
        await controls.click(screen.getByRole('button', { name: /Quay lại/ }));
        expect(screen.getByRole('radio', { name: /Purpose/ })).toBeChecked();
        await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
        await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
        await controls.click(screen.getByRole('radio', { name: '19/10/2026' }));
      }
      const alternative = await screen.findByRole('radio', { name: /08:30 – 09:00/ });
      expect(alternative).not.toBeChecked();
      expect(screen.queryByRole('radio', { name: /08:00 – 08:30/ })).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Thử lại cùng yêu cầu' }),
      ).not.toBeInTheDocument();
      expect(fixture.api().intents.snapshot()).toBeNull();
      expect(availabilityReads).toBe(2);
      expect(posted[1].body).toBe(posted[0].body);
      expect(new Headers(posted[1].headers).get('Idempotency-Key')).toBe(original.key);
      await controls.click(alternative);
      await controls.click(screen.getByRole('button', { name: /Tiếp tục/ }));
      await controls.click(screen.getByRole('button', { name: /Xác nhận đặt lịch/ }));
      await waitFor(() => expect(router.push).toHaveBeenCalled());
      expect(new Headers(posted[2].headers).get('Idempotency-Key')).not.toBe(original.key);
      expect(JSON.parse(String(posted[2].body)).starts_at).toBe('2026-10-19T01:30:00Z');
    },
  );
});
