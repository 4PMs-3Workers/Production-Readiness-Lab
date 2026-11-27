import type {
  AdminBookingConfiguration,
  BookingPolicy,
} from '@/services/admin-configuration-contracts';
import { positiveSmallInt, windowsValid } from '@/services/api/admin-configuration-mapping';

export interface WindowDraft {
  key: string;
  weekday: number;
  opens_at: string;
  closes_at: string;
}
export interface ConfigurationDraft {
  timezone: string;
  start_step_minutes: string;
  booking_horizon_days: string;
  max_active_appointments: number;
  windows: WindowDraft[];
}
let sequence = 0;
export const windowKey = () => `window-${++sequence}`;
export function configurationDraft(value: AdminBookingConfiguration): ConfigurationDraft {
  return {
    timezone: value.timezone,
    start_step_minutes: String(value.start_step_minutes),
    booking_horizon_days: String(value.booking_horizon_days),
    max_active_appointments: value.max_active_appointments,
    windows: value.windows.map((w) => ({ ...w, key: windowKey() })),
  };
}
export const normalizeWallTime = (value: string) =>
  /^\d\d:\d\d$/.test(value) ? `${value}:00` : value;
export function draftPolicy(draft: ConfigurationDraft): BookingPolicy {
  return {
    timezone: draft.timezone,
    start_step_minutes: Number(draft.start_step_minutes),
    booking_horizon_days: Number(draft.booking_horizon_days),
    max_active_appointments: draft.max_active_appointments,
    windows: draft.windows.map((w) => ({
      weekday: w.weekday,
      opens_at: normalizeWallTime(w.opens_at),
      closes_at: normalizeWallTime(w.closes_at),
    })),
  };
}
export function configurationErrors(draft: ConfigurationDraft) {
  const errors: Record<string, string> = {};
  if (
    !draft.timezone ||
    Array.from(draft.timezone).length > 64 ||
    draft.timezone !== draft.timezone.trim()
  )
    errors.timezone = 'Nhập IANA location timezone hoặc UTC, tối đa 64 ký tự.';
  for (const field of ['start_step_minutes', 'booking_horizon_days'] as const) {
    if (!draft[field].trim() || !positiveSmallInt(Number(draft[field])))
      errors[field] = 'Nhập số nguyên từ 1 đến 32767.';
  }
  for (const w of draft.windows) {
    if (
      !windowsValid([
        {
          weekday: w.weekday,
          opens_at: normalizeWallTime(w.opens_at),
          closes_at: normalizeWallTime(w.closes_at),
        },
      ])
    )
      errors[w.key] = 'Giờ mở phải trước giờ đóng trong cùng ngày; nhập giờ, phút và giây hợp lệ.';
  }
  const windows = draft.windows.map((w) => ({
    ...w,
    opens_at: normalizeWallTime(w.opens_at),
    closes_at: normalizeWallTime(w.closes_at),
  }));
  for (let i = 0; i < windows.length; i++) {
    for (let j = i + 1; j < windows.length; j++) {
      const a = windows[i],
        b = windows[j];
      if (
        !errors[a.key] &&
        !errors[b.key] &&
        a.weekday === b.weekday &&
        a.opens_at < b.closes_at &&
        a.closes_at > b.opens_at
      ) {
        errors.windows =
          'Các khoảng cùng ngày bị trùng hoặc giao nhau. Hai khoảng tiếp giáp được phép giữ riêng.';
        errors[a.key] = errors[b.key] = 'Khoảng này trùng hoặc giao với khoảng khác trong ngày.';
      }
    }
  }
  return errors;
}
