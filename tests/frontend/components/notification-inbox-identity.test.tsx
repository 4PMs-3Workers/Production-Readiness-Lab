import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiProvider, useApi } from '@/features/api/api-provider';
import { SessionGate } from '@/features/api/session-gate';
import { NotificationInbox } from '@/features/notifications/notification-inbox';
import { ApiServices } from '@/services/api/client';

const router = vi.hoisted(() => ({ replace: vi.fn() }));
const STUDENT_A = '10000000-0000-4000-8000-000000000001';
const STUDENT_B = '10000000-0000-4000-8000-000000000002';
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/notifications',
  useSearchParams: () => new URLSearchParams(''),
}));

function SessionIdentity() {
  const { session } = useApi();
  return (
    <output data-testid="session-owner">
      {session.status === 'authenticated' ? session.user.id : session.status}
    </output>
  );
}

function renderInbox() {
  let cookieOwner = STUDENT_A;
  let delayList = false;
  let finishList!: () => void;
  let delayMe = false;
  let finishMe!: () => void;
  const listOwners: string[] = [];
  const fetcher = vi.fn<typeof fetch>(async (path, init) => {
    const url = String(path);
    if (url.endsWith('/auth/me')) {
      const ownerAtRequest = cookieOwner;
      const response = Response.json({
        id: ownerAtRequest,
        name: ownerAtRequest,
        email: `${ownerAtRequest}@example.invalid`,
        role: 'STUDENT',
      });
      if (delayMe) {
        delayMe = false;
        return new Promise<Response>((resolve) => {
          finishMe = () => resolve(response);
        });
      }
      return response;
    }
    if (url.endsWith('/booking-context'))
      return Response.json({
        timezone: 'Asia/Ho_Chi_Minh',
        business_date: '2026-10-22',
        min_booking_date: '2026-10-23',
        max_booking_date: '2026-11-22',
      });
    if (url.includes('/notifications?')) {
      const ownerAtRequest = cookieOwner;
      listOwners.push(ownerAtRequest);
      const expectedOwner = new Headers(init?.headers).get('X-Expected-User-ID');
      const response =
        expectedOwner && expectedOwner !== ownerAtRequest
          ? Response.json({ error: { code: 'ACCOUNT_CHANGED' } }, { status: 409 })
          : Response.json({
              page: 1,
              page_size: 20,
              total: 1,
              items: [
                {
                  id: 'notification-a',
                  event_type: 'APPOINTMENT_CREATED',
                  summary: `Private for ${ownerAtRequest}`,
                  created_at: '2026-10-22T03:00:00Z',
                  read_at: null,
                  appointment_id: null,
                  appointment_starts_at: null,
                  appointment_status: null,
                },
              ],
            });
      if (delayList) {
        delayList = false;
        return new Promise<Response>((resolve) => {
          finishList = () => resolve(response);
        });
      }
      return response;
    }
    if (url.endsWith('/auth/csrf'))
      return Response.json({ csrf_token: 'signed-token', user_id: cookieOwner });
    if (url.endsWith('/notifications/notification-a/read'))
      return Response.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
    throw new Error(`Unexpected request: ${url}`);
  });
  render(
    <ApiProvider services={new ApiServices(fetcher)}>
      <SessionIdentity />
      <SessionGate>
        <NotificationInbox />
      </SessionGate>
    </ApiProvider>,
  );
  return {
    switchCookieToB: () => {
      cookieOwner = STUDENT_B;
    },
    switchCookieToA: () => {
      cookieOwner = STUDENT_A;
    },
    delayNextMe: () => {
      delayMe = true;
    },
    hasHeldMe: () => finishMe !== undefined,
    finishMe: () => finishMe(),
    delayNextList: () => {
      delayList = true;
    },
    hasHeldList: () => finishList !== undefined,
    finishList: () => finishList(),
    listOwners,
    fetcher,
  };
}

describe('notification inbox identity boundary', () => {
  it('clears the rendered account when a mark-read preflight sees another cookie owner', async () => {
    const { switchCookieToB } = renderInbox();
    expect(await screen.findByText(`Private for ${STUDENT_A}`)).toBeVisible();

    switchCookieToB();
    fireEvent.click(screen.getByRole('button', { name: 'Đánh dấu đã đọc' }));

    await waitFor(() =>
      expect(screen.queryByText(`Private for ${STUDENT_A}`)).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('session-owner')).not.toHaveTextContent(STUDENT_A);
  });

  it('does not render another account inbox under the previous provider identity', async () => {
    const { switchCookieToB } = renderInbox();
    expect(await screen.findByText(`Private for ${STUDENT_A}`)).toBeVisible();

    switchCookieToB();
    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }));

    await waitFor(() =>
      expect(screen.getByTestId('session-owner')).not.toHaveTextContent(STUDENT_A),
    );
    expect(screen.queryByText(`Private for ${STUDENT_A}`)).not.toBeInTheDocument();
  });

  it('invalidates a list whose response arrives after the shared cookie changes', async () => {
    const { switchCookieToB, delayNextList, finishList, fetcher } = renderInbox();
    expect(await screen.findByText(`Private for ${STUDENT_A}`)).toBeVisible();

    delayNextList();
    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([path]) => String(path).includes('/notifications?')),
      ).toHaveLength(2),
    );
    switchCookieToB();
    finishList();

    await waitFor(() =>
      expect(screen.getByTestId('session-owner')).not.toHaveTextContent(STUDENT_A),
    );
    expect(screen.queryByText(`Private for ${STUDENT_A}`)).not.toBeInTheDocument();
  });

  it('rejects a B-authenticated list after the shared cookie returns to A', async () => {
    const {
      delayNextMe,
      hasHeldMe,
      finishMe,
      delayNextList,
      hasHeldList,
      finishList,
      switchCookieToA,
      switchCookieToB,
      listOwners,
    } = renderInbox();
    expect(await screen.findByText(`Private for ${STUDENT_A}`)).toBeVisible();

    delayNextMe();
    delayNextList();
    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }));
    await waitFor(() => expect(hasHeldMe()).toBe(true));
    switchCookieToB();
    finishMe();
    await waitFor(() => expect(hasHeldList()).toBe(true));
    expect(listOwners.at(-1)).toBe(STUDENT_B);
    switchCookieToA();
    finishList();

    await waitFor(() =>
      expect(screen.getByTestId('session-owner')).not.toHaveTextContent(STUDENT_A),
    );
    expect(screen.queryByText(`Private for ${STUDENT_B}`)).not.toBeInTheDocument();
    fireEvent.focus(window);
    expect(screen.queryByText(`Private for ${STUDENT_B}`)).not.toBeInTheDocument();
  });
});
