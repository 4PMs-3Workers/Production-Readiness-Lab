'use client';

import { useEffect, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useApi } from '@/features/api/api-provider';
import { errorMessage } from '@/services/api/errors';
import styles from '../booking/booking.module.css';

export function safeStaffReturnPath(value: string | null): string {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.includes('\\') ||
    Array.from(value).some((character) => character.charCodeAt(0) <= 32)
  )
    return '/staff/appointments';
  const path = value.split(/[?#]/)[0];
  return ['/staff/appointments', '/staff/preferences', '/staff/unavailability'].includes(path) ||
    /^\/staff\/appointments\/[a-zA-Z0-9-]+$/.test(path)
    ? value
    : '/staff/appointments';
}

export function StaffSessionGate({ children }: { children: ReactNode }) {
  const { session, refreshSession, logout, revision } = useApi();
  const router = useRouter();
  const path = usePathname();
  const search = useSearchParams();

  useEffect(() => {
    if (session.status === 'anonymous') {
      const destination = safeStaffReturnPath(
        `${path}${search.size ? `?${search.toString()}` : ''}`,
      );
      router.replace(`/login?returnTo=${encodeURIComponent(destination)}`);
    }
  }, [session.status, router, path, search]);

  if (session.status === 'error')
    return (
      <div className={styles.feedback} role="alert">
        <p>{errorMessage(session.error)}</p>
        <button
          type="button"
          className={styles.secondaryButton}
          onClick={() => void refreshSession()}
        >
          Kiểm tra lại phiên
        </button>
      </div>
    );
  if (session.status !== 'authenticated')
    return (
      <p className={styles.feedback} role="status">
        Đang kiểm tra phiên đăng nhập…
      </p>
    );
  if (session.user.role !== 'STAFF')
    return (
      <section className={styles.feedback}>
        <h1>Tài khoản chưa được hỗ trợ</h1>
        <p>Vai trò {session.user.role} không có quyền truy cập khu vực nhân viên.</p>
        <button
          type="button"
          className={styles.secondaryButton}
          onClick={() => void logout().catch(() => {})}
        >
          Đăng xuất
        </button>
      </section>
    );

  return (
    <>
      {session.verifying && (
        <p className={styles.feedback} role="status">
          Đang kiểm tra phiên đăng nhập…
        </p>
      )}
      <div
        key={`${session.user.id}:${revision}`}
        hidden={session.verifying}
        inert={session.verifying}
      >
        {children}
      </div>
    </>
  );
}
