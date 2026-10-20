import { isBusinessDate } from './business-time';

interface LocalParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

function partsAt(instant: number, timezone: string): LocalParts {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    calendar: 'iso8601',
    numberingSystem: 'latn',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(instant);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value);
  return {
    year: value('year'),
    month: value('month'),
    day: value('day'),
    hour: value('hour'),
    minute: value('minute'),
  };
}

function sameLocalTime(left: LocalParts, right: LocalParts) {
  return (
    left.year === right.year &&
    left.month === right.month &&
    left.day === right.day &&
    left.hour === right.hour &&
    left.minute === right.minute
  );
}

function offsetLabel(offsetMinutes: number) {
  const sign = offsetMinutes < 0 ? '-' : '+';
  const absolute = Math.abs(offsetMinutes);
  const hours = String(Math.floor(absolute / 60)).padStart(2, '0');
  const minutes = String(absolute % 60).padStart(2, '0');
  return `${sign}${hours}:${minutes}`;
}

/** Convert an HTML local date-time to a unique instant using server business time. */
export function localDateTimeToOffset(value: string, timezone: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match || !isBusinessDate(match[1]))
    throw new RangeError('Ngày giờ không hợp lệ. Hãy nhập lại thời điểm.');

  const hour = Number(match[2]);
  const minute = Number(match[3]);
  if (hour > 23 || minute > 59)
    throw new RangeError('Ngày giờ không hợp lệ. Hãy nhập lại thời điểm.');

  const [year, month, day] = match[1].split('-').map(Number);
  const target: LocalParts = { year, month, day, hour, minute };
  const localAsUtc = Date.UTC(year, month - 1, day, hour, minute);
  const offsets = new Set<number>();

  // Sample both sides of nearby offset changes so a daylight-saving fold can be detected.
  for (let hours = -36; hours <= 36; hours += 3) {
    const sample = localAsUtc + hours * 60 * 60 * 1000;
    const local = partsAt(sample, timezone);
    const representedAsUtc = Date.UTC(
      local.year,
      local.month - 1,
      local.day,
      local.hour,
      local.minute,
    );
    offsets.add(Math.round((representedAsUtc - sample) / 60_000));
  }

  const candidates = new Set<number>();
  for (const offset of offsets) {
    const candidate = localAsUtc - offset * 60_000;
    if (sameLocalTime(partsAt(candidate, timezone), target)) candidates.add(candidate);
  }
  if (candidates.size === 0)
    throw new RangeError(
      'Giờ địa phương này không tồn tại do thay đổi múi giờ. Hãy chọn thời điểm khác.',
    );
  if (candidates.size > 1)
    throw new RangeError('Giờ địa phương này mơ hồ do thay đổi múi giờ. Hãy chọn thời điểm khác.');

  const instant = [...candidates][0];
  const offset = Math.round((localAsUtc - instant) / 60_000);
  return `${match[1]}T${match[2]}:${match[3]}:00${offsetLabel(offset)}`;
}
