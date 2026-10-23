'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import { errorMessage } from '@/services/api/errors';
import type { NotificationPage } from '@/services/notification-contracts';
import styles from './notification-inbox.module.css';

type InboxState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; data: NotificationPage };
type StoredInboxState = { key: string; value: Exclude<InboxState, { status: 'loading' }> };

export function NotificationInbox() {
  const { services, session, revision } = useApi();
  const userKey =
    session.status === 'authenticated'
      ? `${session.user.id}:${session.user.role}:${revision ?? 0}`
      : '';
  const role =
    session.status === 'authenticated' && session.user.role === 'STAFF' ? 'STAFF' : 'STUDENT';
  const expectedUserId = session.status === 'authenticated' ? session.user.id : '';
  const [page, setPage] = useState(1);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [refreshCount, setRefreshCount] = useState(0);
  const [storedState, setStoredState] = useState<StoredInboxState | null>(null);
  const [pendingIds, setPendingIds] = useState<ReadonlySet<string>>(() => new Set());
  const [readError, setReadError] = useState<string | null>(null);
  const [timezone, setTimezone] = useState('Asia/Ho_Chi_Minh');
  const activeUserKey = useRef<string | null>(null);
  const requestKey = `${userKey}:${page}:${unreadOnly}:${refreshCount}`;
  const state: InboxState =
    storedState?.key === requestKey ? storedState.value : { status: 'loading' };

  useEffect(() => {
    activeUserKey.current = userKey;
    return () => {
      if (activeUserKey.current === userKey) activeUserKey.current = null;
    };
  }, [userKey]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    services
      .notificationPage(
        { page, pageSize: 20, unreadOnly },
        { signal: controller.signal, expectedUserId, expectedRole: role },
      )
      .then(
        (data) => {
          if (!active) return;
          const lastPage = Math.max(1, Math.ceil(data.total / data.pageSize));
          if (page > lastPage) {
            setPage(lastPage);
            return;
          }
          setStoredState({ key: requestKey, value: { status: 'success', data } });
        },
        (error: unknown) => {
          if (active && !(error instanceof Error && error.name === 'AbortError'))
            setStoredState({
              key: requestKey,
              value: { status: 'error', message: errorMessage(error) },
            });
        },
      );
    return () => {
      active = false;
      controller.abort();
    };
  }, [services, userKey, expectedUserId, role, page, unreadOnly, refreshCount, requestKey]);

  useEffect(() => {
    const controller = new AbortController();
    const request =
      role === 'STAFF'
        ? services.staffContext({ signal: controller.signal })
        : services.context({ signal: controller.signal });
    request.then(
      (context) => setTimezone(context.timezone),
      () => undefined,
    );
    return () => controller.abort();
  }, [services, userKey, role]);

  const dateFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat('vi-VN', {
        dateStyle: 'medium',
        timeStyle: 'short',
        timeZone: timezone,
      }),
    [timezone],
  );

  function refresh() {
    setReadError(null);
    setRefreshCount((value) => value + 1);
  }

  async function markRead(id: string) {
    setReadError(null);
    setPendingIds((current) => new Set(current).add(id));
    try {
      const result = await services.markNotificationRead(id, {
        expectedUserId,
        expectedRole: role,
      });
      if (activeUserKey.current !== userKey) return;
      if (!unreadOnly)
        setStoredState((current) => {
          if (current?.key !== requestKey || current.value.status !== 'success') return current;
          return {
            key: requestKey,
            value: {
              status: 'success',
              data: {
                ...current.value.data,
                items: current.value.data.items.map((item) =>
                  item.id === id ? { ...item, readAt: result.readAt } : item,
                ),
              },
            },
          };
        });
      setRefreshCount((value) => value + 1);
    } catch (error) {
      if (activeUserKey.current === userKey) setReadError(errorMessage(error));
    } finally {
      if (activeUserKey.current === userKey)
        setPendingIds((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
    }
  }

  const data = state.status === 'success' ? state.data : null;
  const pageCount = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <section className={styles.inbox} aria-labelledby="notification-title">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>HỘP THƯ CÁ NHÂN</p>
          <h1 id="notification-title">Thông báo</h1>
          <p className={styles.intro}>Các cập nhật về lịch hẹn và đề xuất thay đổi của bạn.</p>
        </div>
        <button
          type="button"
          className={styles.secondaryButton}
          onClick={refresh}
          disabled={state.status === 'loading'}
        >
          Làm mới
        </button>
      </header>

      <div className={styles.controls}>
        <label>
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(event) => {
              setUnreadOnly(event.currentTarget.checked);
              setPage(1);
            }}
          />
          Chỉ xem thông báo chưa đọc
        </label>
        {data && <span aria-live="polite">{data.total} thông báo</span>}
      </div>

      {state.status === 'loading' && (
        <p className={styles.feedback} role="status">
          Đang tải thông báo…
        </p>
      )}
      {state.status === 'error' && (
        <div className={styles.feedback} role="alert">
          <p>{state.message}</p>
          <button type="button" className={styles.secondaryButton} onClick={refresh}>
            Thử tải lại
          </button>
        </div>
      )}
      {state.status === 'success' && state.data.items.length === 0 && (
        <p className={styles.empty} role="status">
          {state.data.total === 0
            ? 'Bạn chưa có thông báo nào.'
            : 'Không có thông báo trên trang này.'}
        </p>
      )}
      {state.status === 'success' && state.data.items.length > 0 && (
        <ul className={styles.list} aria-label="Danh sách thông báo">
          {state.data.items.map((item) => {
            const isUnread = item.readAt === null;
            const appointmentPath =
              role === 'STAFF'
                ? `/staff/appointments/${encodeURIComponent(item.appointmentId ?? '')}`
                : `/appointments/${encodeURIComponent(item.appointmentId ?? '')}`;
            return (
              <li key={item.id} className={`${styles.item} ${isUnread ? styles.unread : ''}`}>
                <div className={styles.itemTop}>
                  <span className={isUnread ? styles.unreadLabel : styles.readLabel}>
                    <span aria-hidden="true" className={styles.statusDot} />
                    {isUnread ? 'Chưa đọc' : 'Đã đọc'}
                  </span>
                  <time dateTime={item.createdAt}>
                    {dateFormatter.format(new Date(item.createdAt))}
                  </time>
                </div>
                <p className={styles.summary}>{item.summary}</p>
                {item.appointmentStartsAt && (
                  <p className={styles.appointmentTime}>
                    Lịch hẹn: {dateFormatter.format(new Date(item.appointmentStartsAt))}
                  </p>
                )}
                <div className={styles.actions}>
                  {item.appointmentId && (
                    <Link href={appointmentPath} className={styles.appointmentLink}>
                      Xem lịch hẹn
                    </Link>
                  )}
                  {isUnread && (
                    <button
                      type="button"
                      className={styles.readButton}
                      disabled={pendingIds.has(item.id)}
                      onClick={() => void markRead(item.id)}
                    >
                      {pendingIds.has(item.id) ? 'Đang lưu…' : 'Đánh dấu đã đọc'}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {readError && (
        <div className={styles.readError} role="alert">
          <p>{readError} Chưa thể xác nhận trạng thái đã đọc.</p>
          <button type="button" className={styles.secondaryButton} onClick={refresh}>
            Tải lại trạng thái
          </button>
        </div>
      )}

      {data && data.total > 0 && (
        <nav className={styles.pagination} aria-label="Phân trang thông báo">
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={page <= 1 || state.status === 'loading'}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
          >
            Trang trước
          </button>
          <span>
            Trang {data.page} / {pageCount}
          </span>
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={page >= pageCount || state.status === 'loading'}
            onClick={() => setPage((value) => value + 1)}
          >
            Trang sau
          </button>
        </nav>
      )}
    </section>
  );
}
