import type { ReactNode } from 'react';

import { PortalShell } from '@/components/portal-shell';

export default function HomeLayout({ children }: { children: ReactNode }) {
  return <PortalShell section="home">{children}</PortalShell>;
}
