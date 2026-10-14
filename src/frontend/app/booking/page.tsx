import type { Metadata } from 'next';

import { BookingFlow } from '@/features/booking/booking-flow';

export const metadata: Metadata = { title: 'Đặt lịch tư vấn' };

export default function BookingPage() {
  return <BookingFlow />;
}
