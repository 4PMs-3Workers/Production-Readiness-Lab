'use client';

import type { ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { PortalShell } from '@/components/portal-shell';
import { isMockMode } from '@/lib/application-mode';
import { StaffSessionGate } from './staff-session-gate';

export type StaffPortalSection = 'appointments' | 'preferences' | 'unavailability';

export function StaffPortalLayout({
  children,
  section,
}: {
  children: ReactNode;
  section?: StaffPortalSection;
}) {
  const pathname = usePathname();
  if (isMockMode())
    return (
      <main>
        <h1>Cổng nhân viên chưa được bật</h1>
        <p>Chuyển chế độ ứng dụng sang API để truy cập khu vực dành cho nhân viên.</p>
      </main>
    );
  const activeSection =
    section ??
    (pathname === '/staff/preferences'
      ? 'preferences'
      : pathname === '/staff/unavailability'
        ? 'unavailability'
        : 'appointments');
  return (
    <StaffSessionGate>
      <PortalShell audience="staff" section={activeSection}>
        {children}
      </PortalShell>
    </StaffSessionGate>
  );
}
