import type { Metadata } from 'next';

import { BookingResultView } from '@/features/booking/booking-result';

export const metadata: Metadata = { title: 'Chi tiết lịch đã đặt' };

export default async function AppointmentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BookingResultView bookingId={id} />;
}
