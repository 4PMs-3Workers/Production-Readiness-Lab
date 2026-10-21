import type { Metadata } from 'next';
import { StaffPurposePreferences } from '@/features/staff/preferences/purpose-preferences';

export const metadata: Metadata = { title: 'Nhu cầu tiếp nhận' };

export default function StaffPreferencesPage() {
  return <StaffPurposePreferences />;
}
