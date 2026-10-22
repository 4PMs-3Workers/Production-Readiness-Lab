import type { ReactNode } from 'react';
import { PortalShell } from '@/components/portal-shell';
import { SessionGate } from '@/features/api/session-gate';

export default function NotificationsLayout({ children }: { children: ReactNode }) {
  return (
    <SessionGate>
      <PortalShell section="notifications">{children}</PortalShell>
    </SessionGate>
  );
}
