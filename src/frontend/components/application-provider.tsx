'use client';

import { Suspense, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { isMockMode } from '@/lib/application-mode';
import { DemoProvider } from '@/features/demo/demo-provider';
import { ApiProvider } from '@/features/api/api-provider';
import { SessionGate } from '@/features/api/session-gate';

export function ApplicationProvider({ children }: { children: ReactNode }) {
  if (isMockMode()) return <DemoProvider>{children}</DemoProvider>;
  return (
    <ApiProvider>
      <Suspense fallback={<p role="status">Đang tải cổng sinh viên…</p>}>
        <ProtectedContent>{children}</ProtectedContent>
      </Suspense>
    </ApiProvider>
  );
}
function ProtectedContent({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return pathname === '/login' ||
    pathname === '/' ||
    pathname === '/staff' ||
    pathname.startsWith('/staff/') ? (
    children
  ) : (
    <SessionGate>{children}</SessionGate>
  );
}
