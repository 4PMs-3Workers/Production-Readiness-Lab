import type { AdminPurpose } from '../../src/frontend/services/admin-catalog-contracts';
import type { AdminBookingConfiguration } from '../../src/frontend/services/admin-configuration-contracts';

export const purposeFixture: AdminPurpose = {
  id: '33333333-3333-4333-8333-333333333333',
  code: 'ADVICE',
  name: 'Tư vấn mẫu',
  duration_minutes: 30,
  requires_free_text: true,
  is_active: true,
  updated_at: '2026-11-08T01:00:00.123456Z',
};
export const configurationFixture: AdminBookingConfiguration = {
  timezone: 'Asia/Ho_Chi_Minh',
  start_step_minutes: 15,
  booking_horizon_days: 7,
  windows: [
    { weekday: 1, opens_at: '08:00:15', closes_at: '11:30:15' },
    { weekday: 1, opens_at: '11:30:15', closes_at: '12:00:00' },
  ],
  updated_at: '2026-11-08T01:00:00.654321+00:00',
};
