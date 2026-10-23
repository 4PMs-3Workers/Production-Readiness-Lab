import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiProvider, useApi } from '@/features/api/api-provider';
import { SessionGate } from '@/features/api/session-gate';
import { NotificationInbox } from '@/features/notifications/notification-inbox';
import { ApiServices } from '@/services/api/client';

const router = vi.hoisted(() => ({ replace: vi.fn() }));
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
  let cookieOwner = 'student-a';
  let delayList = false;
  let finishList!: () => void;
  const fetcher = vi.fn<typeof fetch>(async (path) => {
    const url = String(path);
    if (url.endsWith('/auth/me'))
      return Response.json({
        id: cookieOwner,
        name: cookieOwner,
        email: `${cookieOwner}@example.invalid`,
        role: 'STUDENT',
      });
    if (url.endsWith('/booking-context'))
      return Response.json({
        timezone: 'Asia/Ho_Chi_Minh',
        business_date: '2026-10-22',
        min_booking_date: '2026-10-23',
        max_booking_date: '2026-11-22',
      });
    if (url.includes('/notifications?')) {
      const ownerAtRequest = cookieOwner;
      const response = Response.json({
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
      if (delayList)
        return new Promise<Response>((resolve) => {
          finishList = () => resolve(response);
        });
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
      cookieOwner = 'student-b';
    },
    delayNextList: () => {
      delayList = true;
    },
    finishList: () => finishList(),
    fetcher,
  };
}

describe('notification inbox identity boundary', () => {
  it('clears the rendered account when a mark-read preflight sees another cookie owner', async () => {
    const { switchCookieToB } = renderInbox();
    expect(await screen.findByText('Private for student-a')).toBeVisible();

    switchCookieToB();
    fireEvent.click(screen.getByRole('button', { name: 'Đánh dấu đã đọc' }));

    await waitFor(() =>
      expect(screen.queryByText('Private for student-a')).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('session-owner')).not.toHaveTextContent('student-a');
  });

  it('does not render another account inbox under the previous provider identity', async () => {
    const { switchCookieToB } = renderInbox();
    expect(await screen.findByText('Private for student-a')).toBeVisible();

    switchCookieToB();
    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }));

    await waitFor(() =>
      expect(screen.getByTestId('session-owner')).not.toHaveTextContent('student-a'),
    );
    expect(screen.queryByText('Private for student-a')).not.toBeInTheDocument();
  });

  it('invalidates a list whose response arrives after the shared cookie changes', async () => {
    const { switchCookieToB, delayNextList, finishList, fetcher } = renderInbox();
    expect(await screen.findByText('Private for student-a')).toBeVisible();

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
      expect(screen.getByTestId('session-owner')).not.toHaveTextContent('student-a'),
    );
    expect(screen.queryByText('Private for student-a')).not.toBeInTheDocument();
  });
});
