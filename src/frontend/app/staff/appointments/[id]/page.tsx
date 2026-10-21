import type { Metadata } from 'next';
import { StaffAppointmentDetailView } from '@/features/staff/appointments/staff-appointments';

export const metadata: Metadata = { title: 'Chi tiết lịch được giao' };

export default async function StaffAppointmentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <StaffAppointmentDetailView appointmentId={id} />;
}
