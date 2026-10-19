import type { ReactNode } from 'react';
import { PortalShell } from '@/components/portal-shell';

export default function ProfileLayout({ children }: { children: ReactNode }) {
  return <PortalShell section="profile">{children}</PortalShell>;
}
