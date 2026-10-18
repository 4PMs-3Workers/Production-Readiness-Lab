import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { ApplicationProvider } from '@/components/application-provider';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Cổng tư vấn sinh viên',
    template: '%s | Cổng tư vấn sinh viên',
  },
  description: 'Cổng đặt lịch tư vấn dành cho sinh viên.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <ApplicationProvider>{children}</ApplicationProvider>
      </body>
    </html>
  );
}
