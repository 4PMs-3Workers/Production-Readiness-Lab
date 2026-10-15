import type { Metadata } from 'next';

import { BookingHistoryView } from '@/features/booking/booking-history';

export const metadata: Metadata = { title: 'Lịch đã đặt' };

export default function AppointmentsPage() {
  return <BookingHistoryView />;
}
