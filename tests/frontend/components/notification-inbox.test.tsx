import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationInbox } from '@/features/notifications/notification-inbox';
import type { NotificationPage } from '@/services/notification-contracts';

const mockedApi = vi.hoisted(() => ({ value: null as unknown }));
vi.mock('@/features/api/api-provider', () => ({ useApi: () => mockedApi.value }));

const page: NotificationPage = {
  items: [
    {
      id: 'notification-a',
      eventType: 'APPOINTMENT_CREATED',
      summary: 'Lịch hẹn đã được tạo',
      createdAt: '2026-10-22T03:00:00Z',
      readAt: null,
      appointmentId: 'appointment-a',
      appointmentStartsAt: '2026-10-23T03:00:00Z',
      appointmentStatus: 'UPCOMING',
    },
  ],
  page: 1,
  pageSize: 20,
  total: 1,
};

describe('NotificationInbox', () => {
  let services: {
    notificationPage: ReturnType<typeof vi.fn>;
    markNotificationRead: ReturnType<typeof vi.fn>;
    context: ReturnType<typeof vi.fn>;
    staffContext: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    services = {
      notificationPage: vi.fn().mockResolvedValue(page),
      markNotificationRead: vi.fn().mockResolvedValue({
        id: 'notification-a',
        readAt: '2026-10-22T03:04:05Z',
      }),
      context: vi.fn().mockResolvedValue({ timezone: 'Asia/Ho_Chi_Minh' }),
      staffContext: vi.fn().mockResolvedValue({ timezone: 'Asia/Ho_Chi_Minh' }),
    };
    mockedApi.value = {
      services,
      session: {
        status: 'authenticated',
        user: { id: 'student-a', role: 'STUDENT' },
      },
    };
  });

  it('shows the notification, authorized appointment link, and persists read state', async () => {
    render(<NotificationInbox />);

    expect(await screen.findByText('Lịch hẹn đã được tạo')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Xem lịch hẹn' }).getAttribute('href')).toBe(
      '/appointments/appointment-a',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Đánh dấu đã đọc' }));

    await waitFor(() =>
      expect(services.markNotificationRead).toHaveBeenCalledWith('notification-a'),
    );
    expect(await screen.findByText('Đã đọc')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Đánh dấu đã đọc' })).toBeNull();
  });

  it('provides an empty-state message and unread filter', async () => {
    services.notificationPage.mockResolvedValue({ ...page, items: [], total: 0 });
    render(<NotificationInbox />);

    expect(await screen.findByText('Bạn chưa có thông báo nào.')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Chỉ xem thông báo chưa đọc' }));
    await waitFor(() =>
      expect(services.notificationPage).toHaveBeenLastCalledWith(
        { page: 1, pageSize: 20, unreadOnly: true },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
  });

  it('keeps API failures visible with a manual retry action', async () => {
    services.notificationPage.mockRejectedValue(new Error('unavailable'));
    render(<NotificationInbox />);

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Thử tải lại' })).toBeTruthy();
  });

  it('retries a failed inbox request without substituting demo content', async () => {
    services.notificationPage.mockRejectedValueOnce(new Error('network')).mockResolvedValue(page);
    render(<NotificationInbox />);

    fireEvent.click(await screen.findByRole('button', { name: 'Thử tải lại' }));

    expect(await screen.findByText('Lịch hẹn đã được tạo')).toBeTruthy();
    expect(services.notificationPage).toHaveBeenCalledTimes(2);
  });

  it('loads the next page and shows only its items', async () => {
    services.notificationPage.mockResolvedValueOnce({ ...page, total: 21 }).mockResolvedValueOnce({
      ...page,
      page: 2,
      total: 21,
      items: [{ ...page.items[0], id: 'notification-b', summary: 'Thông báo trang hai' }],
    });
    render(<NotificationInbox />);

    fireEvent.click(await screen.findByRole('button', { name: 'Trang sau' }));

    expect(await screen.findByText('Thông báo trang hai')).toBeTruthy();
    expect(screen.queryByText('Lịch hẹn đã được tạo')).toBeNull();
  });

  it('ignores an old account response after the authenticated user changes', async () => {
    let resolveOld!: (value: NotificationPage) => void;
    let resolveNew!: (value: NotificationPage) => void;
    services.notificationPage
      .mockImplementationOnce(
        () => new Promise<NotificationPage>((resolve) => (resolveOld = resolve)),
      )
      .mockImplementationOnce(
        () => new Promise<NotificationPage>((resolve) => (resolveNew = resolve)),
      );
    const view = render(<NotificationInbox />);
    mockedApi.value = {
      services,
      session: { status: 'authenticated', user: { id: 'student-b', role: 'STUDENT' } },
    };
    view.rerender(<NotificationInbox />);
    await waitFor(() => expect(services.notificationPage).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveNew({
        ...page,
        items: [{ ...page.items[0], id: 'notification-b', summary: 'Thông báo của B' }],
      });
      resolveOld(page);
    });

    expect(await screen.findByText('Thông báo của B')).toBeTruthy();
    expect(screen.queryByText('Lịch hẹn đã được tạo')).toBeNull();
  });

  it('clears an uncertain read warning after reloading confirmed state', async () => {
    services.markNotificationRead.mockRejectedValue(new Error('connection dropped'));
    services.notificationPage.mockResolvedValueOnce(page).mockResolvedValueOnce({
      ...page,
      items: [{ ...page.items[0], readAt: '2026-10-22T03:04:05Z' }],
    });
    render(<NotificationInbox />);

    fireEvent.click(await screen.findByRole('button', { name: 'Đánh dấu đã đọc' }));
    expect(await screen.findByText(/Chưa thể xác nhận trạng thái đã đọc/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Tải lại trạng thái' }));

    expect(await screen.findByText('Đã đọc')).toBeTruthy();
    expect(screen.queryByText(/Chưa thể xác nhận trạng thái đã đọc/)).toBeNull();
  });
});
