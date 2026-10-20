import type { Metadata } from 'next';
import { StaffUnavailabilityManager } from '@/features/staff/unavailability/staff-unavailability';

export const metadata: Metadata = { title: 'Thời gian không nhận lịch' };

export default function StaffUnavailabilityPage() {
  return <StaffUnavailabilityManager />;
}
