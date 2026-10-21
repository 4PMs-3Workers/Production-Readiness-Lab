import type { Metadata } from 'next';
import { StaffAppointmentList } from '@/features/staff/appointments/staff-appointments';

export const metadata: Metadata = { title: 'Lịch được giao' };

export default function StaffAppointmentsPage() {
  return <StaffAppointmentList />;
}
