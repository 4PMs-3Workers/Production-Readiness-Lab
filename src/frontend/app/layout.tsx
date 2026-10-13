import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { AppShell } from '@/components/app-shell';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Cổng tư vấn sinh viên',
    template: '%s | Cổng tư vấn sinh viên',
  },
  description: 'Bản demo cổng đặt lịch tư vấn dành cho sinh viên.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
