import type { ReactNode } from 'react';
import { StaffPortalLayout } from '@/features/staff/staff-layout';

export default function StaffLayout({ children }: { children: ReactNode }) {
  return <StaffPortalLayout>{children}</StaffPortalLayout>;
}
