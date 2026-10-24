import { formatBusinessTime, getBusinessDate } from './business-time';
import { localDateTimeToOffset } from './staff-time';

export function proposalDateTime(instant: string, timezone: string) {
  return {
    date: getBusinessDate(instant, timezone),
    time: formatBusinessTime(instant, timezone),
  };
}

/** Keep an unchanged server value byte for byte, including its microsecond precision. */
export function proposalTimestamp(
  date: string,
  time: string,
  timezone: string,
  original?: string,
): string {
  if (original) {
    const previous = proposalDateTime(original, timezone);
    if (previous.date === date && previous.time === time) return original;
  }
  return localDateTimeToOffset(`${date}T${time}`, timezone);
}
