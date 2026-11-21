import type {
  AdminBookingConfiguration,
  BookingPolicy,
  WeeklyWindow,
} from '@/services/admin-configuration-contracts';
import { isBusinessDate } from '@/lib/business-time';
import { ApiError } from './errors';

export function requireAdminContract(condition: unknown): asserts condition {
  if (!condition) throw new ApiError('SERVICE_UNAVAILABLE');
}
export function adminRecord(value: unknown): Record<string, unknown> {
  requireAdminContract(value && typeof value === 'object' && !Array.isArray(value));
  return value as Record<string, unknown>;
}
export function positiveSmallInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 32767;
}
export function revisionTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const match =
    /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(
      value,
    );
  return Boolean(
    match &&
    isBusinessDate(match[1]) &&
    Number(match[2]) < 24 &&
    Number(match[3]) < 60 &&
    Number(match[4]) < 60 &&
    Number(match[5] ?? 0) < 24 &&
    Number(match[6] ?? 0) < 60 &&
    Number.isFinite(Date.parse(value)),
  );
}
export const wallTimePattern = /^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$/;
export function canonicalWindows(windows: readonly WeeklyWindow[]): WeeklyWindow[] {
  return windows
    .map((w) => ({ weekday: w.weekday, opens_at: w.opens_at, closes_at: w.closes_at }))
    .sort(
      (a, b) =>
        a.weekday - b.weekday ||
        a.opens_at.localeCompare(b.opens_at) ||
        a.closes_at.localeCompare(b.closes_at),
    );
}
export function windowsValid(windows: readonly WeeklyWindow[]) {
  if (
    !windows.every(
      (w) =>
        Number.isInteger(w.weekday) &&
        w.weekday >= 1 &&
        w.weekday <= 7 &&
        wallTimePattern.test(w.opens_at) &&
        wallTimePattern.test(w.closes_at) &&
        w.opens_at < w.closes_at,
    )
  )
    return false;
  const ordered = canonicalWindows(windows);
  return ordered.every(
    (w, i) =>
      i === 0 || ordered[i - 1].weekday !== w.weekday || ordered[i - 1].closes_at <= w.opens_at,
  );
}
export function copyPolicy(value: BookingPolicy): BookingPolicy {
  return {
    timezone: value.timezone,
    start_step_minutes: value.start_step_minutes,
    booking_horizon_days: value.booking_horizon_days,
    windows: canonicalWindows(value.windows),
  };
}
export function policyMatches(a: BookingPolicy, b: BookingPolicy) {
  return JSON.stringify(copyPolicy(a)) === JSON.stringify(copyPolicy(b));
}
export function parseAdminConfiguration(value: unknown): AdminBookingConfiguration {
  const data = adminRecord(value);
  requireAdminContract(
    typeof data.timezone === 'string' &&
      data.timezone.length > 0 &&
      data.timezone.length <= 64 &&
      positiveSmallInt(data.start_step_minutes) &&
      positiveSmallInt(data.booking_horizon_days) &&
      revisionTimestamp(data.updated_at) &&
      Array.isArray(data.windows),
  );
  const windows = data.windows.map((item: unknown): WeeklyWindow => {
    const w = adminRecord(item);
    requireAdminContract(
      typeof w.weekday === 'number' &&
        typeof w.opens_at === 'string' &&
        typeof w.closes_at === 'string',
    );
    return { weekday: w.weekday, opens_at: w.opens_at, closes_at: w.closes_at };
  });
  requireAdminContract(windowsValid(windows));
  return {
    timezone: data.timezone,
    start_step_minutes: data.start_step_minutes,
    booking_horizon_days: data.booking_horizon_days,
    windows: canonicalWindows(windows),
    updated_at: data.updated_at,
  };
}
