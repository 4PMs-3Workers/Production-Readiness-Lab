import type { ReactNode } from 'react';

import { PortalShell } from '@/components/portal-shell';

export default function AppointmentsLayout({ children }: { children: ReactNode }) {
  return <PortalShell section="history">{children}</PortalShell>;
}
