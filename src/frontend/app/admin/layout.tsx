import type { ReactNode } from 'react';
import { AdminPortalLayout } from '@/features/admin/admin-layout';

export default function AdminLayout({ children }: { children: ReactNode }) {
  return <AdminPortalLayout>{children}</AdminPortalLayout>;
}
