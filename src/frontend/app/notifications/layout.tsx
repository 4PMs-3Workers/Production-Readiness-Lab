import type { ReactNode } from 'react';
import { PortalShell } from '@/components/portal-shell';

export default function NotificationsLayout({ children }: { children: ReactNode }) {
  return <PortalShell section="notifications">{children}</PortalShell>;
}
