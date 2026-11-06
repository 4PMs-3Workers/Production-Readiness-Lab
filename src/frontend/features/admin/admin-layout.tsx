'use client';

import type { ReactNode } from 'react';
import { isMockMode } from '@/lib/application-mode';
import { PortalShell } from '@/components/portal-shell';
import { AdminSessionGate } from './admin-session-gate';

export function AdminPortalLayout({ children }: { children: ReactNode }) {
  if (isMockMode())
    return (
      <main>
        <h1>Cổng quản trị chưa được bật</h1>
        <p>Chuyển chế độ ứng dụng sang API để sử dụng quản lý tài khoản.</p>
      </main>
    );
  return (
    <AdminSessionGate>
      <PortalShell audience="admin" section="accounts">
        {children}
      </PortalShell>
    </AdminSessionGate>
  );
}
