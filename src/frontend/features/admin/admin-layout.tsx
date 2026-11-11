'use client';

import type { ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { isMockMode } from '@/lib/application-mode';
import { PortalShell } from '@/components/portal-shell';
import { AdminSessionGate } from './admin-session-gate';
import { AdminWorkspace } from './admin-workspace';
import { adminSectionFor } from './admin-routes';
import { AdminRecovery } from './admin-recovery';

export function AdminPortalLayout({ children }: { children: ReactNode }) {
  const path = usePathname();
  if (isMockMode())
    return (
      <main>
        <h1>Cổng quản trị chưa được bật</h1>
        <p>Chuyển chế độ ứng dụng sang API để sử dụng cổng quản trị.</p>
      </main>
    );
  return (
    <AdminSessionGate>
      <AdminWorkspace>
        <PortalShell audience="admin" section={adminSectionFor(path)}>
          <AdminRecovery />
          {children}
        </PortalShell>
      </AdminWorkspace>
    </AdminSessionGate>
  );
}
