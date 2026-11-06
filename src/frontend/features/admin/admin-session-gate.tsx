'use client';

import { useEffect, type ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useApi } from '@/features/api/api-provider';
import { errorMessage } from '@/services/api/errors';

export function safeAdminReturnPath(value: string | null): string {
  if (!value || value.includes('\\') || Array.from(value).some((c) => c.charCodeAt(0) <= 32))
    return '/admin/accounts';
  return value === '/admin' || value === '/admin/accounts' ? value : '/admin/accounts';
}

export function AdminSessionGate({ children }: { children: ReactNode }) {
  const { session, refreshSession, logout, revision } = useApi();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (session.status === 'anonymous')
      router.replace(`/login?returnTo=${encodeURIComponent(safeAdminReturnPath(path))}`);
  }, [session.status, path, router]);
  if (session.status === 'error')
    return (
      <section role="alert">
        <p>{errorMessage(session.error)}</p>
        <button type="button" onClick={() => void refreshSession()}>
          Kiểm tra lại phiên
        </button>
      </section>
    );
  if (session.status !== 'authenticated')
    return <p role="status">Đang kiểm tra phiên đăng nhập…</p>;
  if (session.user.role !== 'ADMIN')
    return (
      <section>
        <h1>Không có quyền quản trị</h1>
        <p>Khu vực này chỉ dành cho ADMIN.</p>
        <button type="button" onClick={() => void logout().catch(() => {})}>
          Đăng xuất
        </button>
      </section>
    );
  return (
    <>
      {session.verifying && <p role="status">Đang kiểm tra phiên đăng nhập…</p>}
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
