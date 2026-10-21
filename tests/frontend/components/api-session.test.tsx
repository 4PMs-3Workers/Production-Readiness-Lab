import { act, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiProvider, useApi, useBookingIntent } from '@/features/api/api-provider';
import { SessionGate, safeReturnPath } from '@/features/api/session-gate';
import { ApiServices } from '@/services/api/client';
import { ApiResult } from '@/features/api/api-appointments';
import { ApiLogin } from '@/features/api/api-login';
import { StaffSessionGate } from '@/features/staff/staff-session-gate';

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/appointments',
  useSearchParams: () => new URLSearchParams(''),
}));
const user = { id: 'a', name: 'Fictional Student A', email: 'a@example.invalid', role: 'STUDENT' };
const error = (code: string, status: number) => Response.json({ error: { code } }, { status });
const json = (value: unknown) => Response.json(value);
beforeEach(() => router.replace.mockClear());

function PrivateView() {
  const { session, logout, intents } = useApi();
  const intent = useBookingIntent();
  return (
    <>
      <p>private:{session.status === 'authenticated' ? session.user.displayName : ''}</p>
      {intent && <p>pending:{intent.body.description}</p>}
      <button onClick={() => void logout().catch(() => {})}>logout</button>
      <button
        onClick={() =>
          intents.prepare('a', {
            purpose_id: 'p',
            staff_id: 's',
            starts_at: '2026-10-20T01:00:00Z',
            description: 'fictional',
          })
        }
      >
        draft
      </button>
    </>
  );
}

describe('API session boundaries', () => {
  it.each([
    ['another STAFF account', { id: 'staff-b', role: 'STAFF' }],
    ['a STUDENT role', { id: 'staff-a', role: 'STUDENT' }],
  ])('hides private staff content when mutation preflight detects %s', async (_, changed) => {
    const staff = {
      id: 'staff-a',
      name: 'Fictional Staff A',
      email: 'staff-a@example.invalid',
      role: 'STAFF',
    };
    let switched = false;
    const fetcher = vi.fn<typeof fetch>(async (path) => {
      if (String(path).endsWith('/auth/me'))
        return json(switched ? { ...staff, ...changed } : staff);
      if (String(path).endsWith('/auth/csrf'))
        return json({ csrf_token: 'csrf', user_id: changed.id });
      throw new Error(`Unexpected request: ${path}`);
    });
    const services = new ApiServices(fetcher);
    let staffChanges!: ReturnType<typeof useApi>['staffChanges'];
    function StaffPrivateView() {
      const api = useApi();
      useEffect(() => {
        staffChanges = api.staffChanges;
      }, [api.staffChanges]);
      return (
        <>
          <p>Nội dung trao đổi riêng của STAFF A</p>
          <button
            onClick={() =>
              void api.services
                .recordStaffOutcome('appointment-1', 'COMPLETED', {
                  expectedUserId: 'staff-a',
                })
                .catch(() => {})
            }
          >
            Ghi kết quả
          </button>
        </>
      );
    }
    render(
      <ApiProvider services={services}>
        <StaffSessionGate>
          <StaffPrivateView />
        </StaffSessionGate>
      </ApiProvider>,
    );
    await screen.findByText('Nội dung trao đổi riêng của STAFF A');
    act(() => {
      staffChanges.begin({
        userId: 'staff-a',
        appointmentId: 'appointment-1',
        status: 'COMPLETED',
      });
    });
    expect(staffChanges.get('staff-a', 'appointment-1')).not.toBeNull();
    switched = true;
    await userEvent.click(screen.getByRole('button', { name: 'Ghi kết quả' }));

    await waitFor(() =>
      expect(screen.queryByText('Nội dung trao đổi riêng của STAFF A')).not.toBeInTheDocument(),
    );
    expect(fetcher.mock.calls.every(([path]) => !String(path).endsWith('/outcome'))).toBe(true);
    expect(staffChanges.get('staff-a', 'appointment-1')).toBeNull();
    expect(router.replace).toHaveBeenCalled();
  });

  it.each(['same-student', 'different-student', 'logout'])(
    'keeps an unresolved result private through expiry and handles %s recovery',
    async (recovery) => {
      let active = user;
      let expired = false;
      const posted: RequestInit[] = [];
      const services = new ApiServices(
        vi.fn<typeof fetch>(async (path, init) => {
          if (String(path).endsWith('/login')) {
            expired = false;
            return json(active);
          }
          if (String(path).endsWith('/logout')) return new Response(null, { status: 204 });
          if (expired) return error('INVALID_SESSION', 401);
          if (String(path).endsWith('/csrf'))
            return json({ csrf_token: 'csrf', user_id: active.id });
          if (String(path).endsWith('/me')) return json(active);
          posted.push(init!);
          if (posted.length === 1) throw new TypeError('Committed response lost');
          return json({ id: 'receipt', status: 'UPCOMING', created_at: '2026-10-18T01:00:00Z' });
        }),
      );
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
            <PrivateView />
          </SessionGate>
        </ApiProvider>,
      );
      await screen.findByText('private:Fictional Student A');
      await userEvent.click(screen.getByRole('button', { name: 'draft' }));
      const original = api.intents.snapshot()!;
      await act(async () => {
        await expect(api.intents.submit(services, user.id)).rejects.toMatchObject({
          code: 'NETWORK_ERROR',
        });
      });
      expired = true;
      await act(async () => {
        await expect(api.intents.submit(services, user.id)).rejects.toMatchObject({
          code: 'INVALID_SESSION',
        });
      });
      expect(api.session.status).toBe('anonymous');
      expect(screen.queryByText(/private:|pending:/)).not.toBeInTheDocument();
      expect(api.intents.snapshot()).toMatchObject({ key: original.key, phase: 'unknown' });
      if (recovery === 'logout') {
        // Explicit logout discards even an unresolved intent; expired sessions may return 401.
        await act(async () => api.logout());
        expect(api.intents.snapshot()).toBeNull();
        return;
      }
      if (recovery === 'different-student') active = { ...user, id: 'b', name: 'Student B' };
      await act(async () => api.login({ email: active.email, password: 'fictional' }));
      if (recovery === 'different-student') {
        expect(api.intents.snapshot()).toBeNull();
        expect(screen.queryByText('pending:fictional')).not.toBeInTheDocument();
        expect(posted).toHaveLength(1);
      } else {
        expect(screen.getByText('pending:fictional')).toBeVisible();
        await act(async () => {
          await expect(api.intents.submit(services, user.id)).resolves.toMatchObject({
            id: 'receipt',
          });
        });
        expect(posted).toHaveLength(2);
        expect(posted[1].body).toBe(posted[0].body);
        expect(new Headers(posted[1].headers).get('Idempotency-Key')).toBe(original.key);
      }
    },
  );

  it('opens booking instructions while signed out without navigating to a protected route', async () => {
    const services = new ApiServices(
      vi.fn<typeof fetch>(async () => error('INVALID_SESSION', 401)),
    );
    render(
      <ApiProvider services={services}>
        <ApiLogin />
      </ApiProvider>,
    );
    await screen.findByLabelText('Email');
    const instruction = screen.getByText('Chọn nhu cầu tư vấn và người tư vấn phù hợp.');
    expect(instruction).not.toBeVisible();
    await userEvent.click(screen.getByText('Cách đặt lịch tư vấn'));
    expect(instruction).toBeVisible();
    expect(screen.getByRole('form', { name: 'Đăng nhập' })).toBeVisible();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it('retains the pending body through a failed focus check and same-student session recovery', async () => {
    let calls = 0;
    const services = new ApiServices(
      vi.fn<typeof fetch>(async () =>
        ++calls === 2 ? error('SERVICE_UNAVAILABLE', 503) : json(user),
      ),
    );
    render(
      <ApiProvider services={services}>
        <SessionGate>
          <PrivateView />
        </SessionGate>
      </ApiProvider>,
    );
    await screen.findByText('private:Fictional Student A');
    await userEvent.click(screen.getByRole('button', { name: 'draft' }));
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Dịch vụ tạm thời');
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra lại phiên' }));
    expect(await screen.findByText('pending:fictional')).toBeVisible();
  });
  it('retains unsent form state after same-account focus verification', async () => {
    let finish!: (response: Response) => void;
    let calls = 0;
    const services = new ApiServices(
      vi.fn<typeof fetch>(() =>
        ++calls === 1
          ? Promise.resolve(json(user))
          : new Promise((resolve) => {
              finish = resolve;
            }),
      ),
    );
    render(
      <ApiProvider services={services}>
        <SessionGate>
          <label>
            Unsent draft
            <input aria-label="Unsent draft" defaultValue="" />
          </label>
        </SessionGate>
      </ApiProvider>,
    );
    const input = await screen.findByLabelText('Unsent draft');
    await userEvent.type(input, 'fictional draft');
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(input).not.toBeVisible();
    await act(async () => finish(json(user)));
    expect(await screen.findByLabelText('Unsent draft')).toHaveValue('fictional draft');
    expect(screen.getByLabelText('Unsent draft')).toBeVisible();
  });
  it('hides previous private data during focus verification and clears pending bodies on an account switch', async () => {
    let finish!: (response: Response) => void;
    let calls = 0;
    const services = new ApiServices(
      vi.fn<typeof fetch>(() =>
        ++calls === 1
          ? Promise.resolve(json(user))
          : new Promise((resolve) => {
              finish = resolve;
            }),
      ),
    );
    render(
      <ApiProvider services={services}>
        <SessionGate>
          <PrivateView />
        </SessionGate>
      </ApiProvider>,
    );
    await screen.findByText('private:Fictional Student A');
    await userEvent.click(screen.getByRole('button', { name: 'draft' }));
    expect(screen.getByText('pending:fictional')).toBeVisible();
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(screen.getByText('private:Fictional Student A')).not.toBeVisible();
    await act(async () => finish(json({ ...user, id: 'b', name: 'Fictional Student B' })));
    expect(await screen.findByText('private:Fictional Student B')).toBeVisible();
    expect(screen.queryByText('pending:fictional')).not.toBeInTheDocument();
  });
  it('hides private UI during bootstrap and redirects unauthenticated deep links', async () => {
    let finish!: (value: Response) => void;
    const services = new ApiServices(
      vi.fn<typeof fetch>(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      ),
    );
    render(
      <ApiProvider services={services}>
        <SessionGate>
          <PrivateView />
        </SessionGate>
      </ApiProvider>,
    );
    expect(screen.queryByText(/private:/)).not.toBeInTheDocument();
    await act(async () => finish(error('INVALID_SESSION', 401)));
    await waitFor(() =>
      expect(router.replace).toHaveBeenCalledWith('/login?returnTo=%2Fappointments'),
    );
    expect(screen.queryByText(/private:/)).not.toBeInTheDocument();
  });
  it('does not impersonate unsupported roles', async () => {
    const services = new ApiServices(
      vi.fn<typeof fetch>(async () => json({ ...user, role: 'STAFF' })),
    );
    render(
      <ApiProvider services={services}>
        <SessionGate>
          <PrivateView />
        </SessionGate>
      </ApiProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' }),
    ).toBeVisible();
    expect(screen.queryByText(/private:/)).not.toBeInTheDocument();
  });
  it('clears private UI on failed logout and reports failure without claiming revocation', async () => {
    const services = new ApiServices(
      vi.fn<typeof fetch>(async (path) =>
        String(path).endsWith('/me')
          ? json(user)
          : String(path).endsWith('/csrf')
            ? json({ csrf_token: 'csrf' })
            : error('SERVICE_UNAVAILABLE', 503),
      ),
    );
    render(
      <ApiProvider services={services}>
        <SessionGate>
          <PrivateView />
        </SessionGate>
      </ApiProvider>,
    );
    await screen.findByText('private:Fictional Student A');
    await userEvent.click(screen.getByRole('button', { name: 'logout' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Dịch vụ tạm thời');
    expect(screen.queryByText(/private:/)).not.toBeInTheDocument();
    expect(router.replace).not.toHaveBeenCalled();
  });
  it.each([
    'https://evil.invalid',
    '//evil.invalid',
    '/\\evil.invalid',
    '/login',
    '/booking/../../evil',
  ])('rejects unsafe return path %s', (value) => expect(safeReturnPath(value)).toBe('/home'));
  it('preserves authorized internal result/detail return paths', () => {
    expect(safeReturnPath('/booking/result?id=abc')).toBe('/booking/result?id=abc');
    expect(safeReturnPath('/appointments/abc')).toBe('/appointments/abc');
  });
});

describe('current appointment retrieval', () => {
  const context = {
    timezone: 'Asia/Ho_Chi_Minh',
    business_date: '2026-10-17',
    min_booking_date: '2026-10-18',
    max_booking_date: '2026-11-16',
  };
  const appointment = {
    id: 'appointment-a',
    purpose_id: 'inactive-purpose',
    purpose_code: 'CUSTOM',
    purpose_name: 'Historical Purpose',
    staff_id: 'inactive-staff',
    staff_name: 'Historical Advisor',
    duration_minutes: 45,
    starts_at: '2026-10-20T01:00:00.123456Z',
    ends_at: '2026-10-20T01:45:00.123456Z',
    created_at: '2026-10-17T02:00:00Z',
    location: null,
    status: 'CANCELLED',
    description: null,
    cancelled_at: '2026-10-17T03:00:00Z',
    cancel_reason: null,
  };
  it('renders current cancelled state and historical display data without querying the active catalog', async () => {
    const fetcher = vi.fn<typeof fetch>(async (path) =>
      String(path).endsWith('/me')
        ? json(user)
        : String(path).endsWith('/booking-context')
          ? json(context)
          : json(appointment),
    );
    render(
      <ApiProvider services={new ApiServices(fetcher)}>
        <ApiResult bookingId="appointment-a" />
      </ApiProvider>,
    );
    expect(await screen.findByText('Đã hủy')).toBeVisible();
    expect(screen.getByText('Historical Purpose')).toBeVisible();
    expect(screen.getByText('Historical Advisor')).toBeVisible();
    expect(screen.getByText('45 phút')).toBeVisible();
    expect(screen.getByText('08:00 – 08:45')).toBeVisible();
    expect(fetcher.mock.calls.every(([path]) => !String(path).includes('/purposes'))).toBe(true);
  });
  it('retains a confirmed identifier after detail failure and retries GET without POSTing', async () => {
    let detailAttempts = 0;
    const fetcher = vi.fn<typeof fetch>(async (path) =>
      String(path).endsWith('/me')
        ? json(user)
        : String(path).endsWith('/booking-context')
          ? json(context)
          : ++detailAttempts === 1
            ? error('SERVICE_UNAVAILABLE', 503)
            : json(appointment),
    );
    render(
      <ApiProvider services={new ApiServices(fetcher)}>
        <ApiResult bookingId="appointment-a" />
      </ApiProvider>,
    );
    expect(await screen.findByText(/Mã lịch hẹn: appointment-a/)).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại' }));
    expect(await screen.findByText('Đã hủy')).toBeVisible();
    expect(fetcher.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });
  it('shows the same inaccessible state for missing and foreign appointments', async () => {
    const services = new ApiServices(
      vi.fn<typeof fetch>(async (path) =>
        String(path).endsWith('/me')
          ? json(user)
          : String(path).endsWith('/booking-context')
            ? json(context)
            : error('NOT_FOUND', 404),
      ),
    );
    render(
      <ApiProvider services={services}>
        <ApiResult bookingId="foreign-id" />
      </ApiProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'Không tìm thấy lịch hẹn.' })).toBeVisible();
    expect(screen.queryByText('Historical Purpose')).not.toBeInTheDocument();
  });
});
