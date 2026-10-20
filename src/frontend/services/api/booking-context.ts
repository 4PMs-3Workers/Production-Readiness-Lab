import { isBusinessDate } from '@/lib/business-time';
import type { BookingContext } from '@/types/api';
import { ApiError } from './errors';

function unavailable(): never {
  throw new ApiError('SERVICE_UNAVAILABLE', 503);
}

/** Validate and normalize the booking-context payload shared by booking and history views. */
export function parseBookingContext(value: unknown): BookingContext {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return unavailable();
  const data = value as Record<string, unknown>;
  const timezone = data.timezone;
  const businessDate = data.businessDate ?? data.business_date;
  const minBookingDate = data.minBookingDate ?? data.min_booking_date;
  const maxBookingDate = data.maxBookingDate ?? data.max_booking_date;
  if (
    typeof timezone !== 'string' ||
    !timezone.trim() ||
    typeof businessDate !== 'string' ||
    !isBusinessDate(businessDate) ||
    typeof minBookingDate !== 'string' ||
    !isBusinessDate(minBookingDate) ||
    typeof maxBookingDate !== 'string' ||
    !isBusinessDate(maxBookingDate) ||
    minBookingDate > maxBookingDate
  )
    return unavailable();
  try {
    new Intl.DateTimeFormat('en', { timeZone: timezone });
  } catch {
    return unavailable();
  }
  return { timezone, businessDate, minBookingDate, maxBookingDate };
}
