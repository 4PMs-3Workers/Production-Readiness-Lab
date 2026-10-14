import type { ReactNode } from 'react';

import { PortalShell } from '@/components/portal-shell';

export default function BookingLayout({ children }: { children: ReactNode }) {
  return <PortalShell>{children}</PortalShell>;
}
