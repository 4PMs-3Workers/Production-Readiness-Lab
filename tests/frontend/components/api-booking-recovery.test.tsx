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
  render(
    <ApiProvider services={services}>
      <Capture />
      <SessionGate>
        <ApiBookingFlow />
      </SessionGate>
    </ApiProvider>,
  );
  return { services, api: () => api };
}

describe('booking recovery boundaries', () => {
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

  it('unlocks the wizard and refreshes slots after an unknown request receives BOOKING_CONFLICT', async () => {
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
          return json({ items: [{ id: 's', name: 'Advisor' }], page: 1, page_size: 20, total: 1 });
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
    await controls.click(await screen.findByRole('button', { name: 'Thử lại cùng yêu cầu' }));
    const alternative = await screen.findByRole('radio', { name: /08:30 – 09:00/ });
    expect(alternative).not.toBeChecked();
    expect(screen.queryByRole('radio', { name: /08:00 – 08:30/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Thử lại cùng yêu cầu' })).not.toBeInTheDocument();
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
  });
});
