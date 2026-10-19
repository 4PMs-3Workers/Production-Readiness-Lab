export const BUSINESS_TIME_ZONE = 'Asia/Ho_Chi_Minh' as const;

export function formatTimeZoneLabel(timezone: string): string {
  if (timezone === BUSINESS_TIME_ZONE || timezone === 'Asia/Saigon') return 'Giờ Việt Nam (UTC+7)';
  return new Intl.DateTimeFormat('vi-VN', {
    timeZone: timezone,
    timeZoneName: 'longGeneric',
  })
    .formatToParts(0)
    .find((part) => part.type === 'timeZoneName')!.value;
}

export function isBusinessDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function parseInstant(value: string): Date {
  const match = /^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.exec(
    value,
  );
  const instant = new Date(value);
  if (!match || !isBusinessDate(match[1]) || !Number.isFinite(instant.getTime())) {
    throw new RangeError('Expected a valid ISO 8601 timestamp with Z or an explicit offset.');
  }
  return instant;
}

export function getBusinessDate(instant: string, timezone: string = BUSINESS_TIME_ZONE): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(parseInstant(instant));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((value) => value.type === type)!.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export function addCalendarDays(date: string, days: number): string {
  if (!isBusinessDate(date) || !Number.isInteger(days)) {
    throw new RangeError('Expected a business date and an integer day offset.');
  }
  // UTC is used only for calendar arithmetic, never for the displayed business date.
  const shifted = new Date(`${date}T00:00:00.000Z`);
  shifted.setUTCDate(shifted.getUTCDate() + days);
  const result = shifted.toISOString().slice(0, 10);
  if (!isBusinessDate(result))
    throw new RangeError('Business date is outside the supported range.');
  return result;
}

export function getBookingDates(now: string): string[] {
  const today = getBusinessDate(now);
  return Array.from({ length: 7 }, (_, index) => addCalendarDays(today, index + 1));
}

export function formatBusinessDate(date: string): string {
  if (!isBusinessDate(date)) throw new RangeError('Expected a business date in YYYY-MM-DD format.');
  return new Intl.DateTimeFormat('vi-VN', {
    timeZone: BUSINESS_TIME_ZONE,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(new Date(`${date}T00:00:00+07:00`));
}

export function formatBusinessTime(instant: string, timezone: string = BUSINESS_TIME_ZONE): string {
  return new Intl.DateTimeFormat('vi-VN', {
    timeZone: timezone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(parseInstant(instant));
}
