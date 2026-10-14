import type { Metadata } from 'next';

import { BookingResultView } from '@/features/booking/booking-result';

export const metadata: Metadata = { title: 'Kết quả đặt lịch' };

export default function BookingResultPage() {
  return <BookingResultView />;
}
