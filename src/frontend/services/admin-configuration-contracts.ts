export interface WeeklyWindow {
  readonly weekday: number;
  readonly opens_at: string;
  readonly closes_at: string;
}
export interface BookingPolicy {
  readonly timezone: string;
  readonly start_step_minutes: number;
  readonly booking_horizon_days: number;
  readonly max_active_appointments: number;
  readonly windows: readonly WeeklyWindow[];
}
export interface AdminBookingConfiguration extends BookingPolicy {
  readonly updated_at: string;
}
export interface BookingConfigurationReplaceBody extends BookingPolicy {
  readonly expected_updated_at: string;
}
