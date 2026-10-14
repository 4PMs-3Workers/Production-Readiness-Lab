// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  addCalendarDays,
  formatBusinessDate,
  formatBusinessTime,
  getBookingDates,
  getBusinessDate,
  isBusinessDate,
  parseInstant,
} from '@/lib/business-time';

describe('business time', () => {
  it('uses the Vietnamese calendar day at the UTC date boundary', () => {
    expect(getBusinessDate('2026-10-13T16:59:59Z')).toBe('2026-10-13');
    expect(getBusinessDate('2026-10-13T17:00:00Z')).toBe('2026-10-14');
    expect(getBusinessDate('2026-10-13T10:30:00-07:00')).toBe('2026-10-14');
    expect(formatBusinessTime('2026-10-13T17:30:00Z')).toBe('00:30');
    expect(formatBusinessDate('2026-10-14')).toBe('14/10/2026');
  });

  it('includes both D+1 and D+7, excluding today and D+8', () => {
    expect(getBookingDates('2026-10-13T09:00:00+07:00')).toEqual([
      '2026-10-14',
      '2026-10-15',
      '2026-10-16',
      '2026-10-17',
      '2026-10-18',
      '2026-10-19',
      '2026-10-20',
    ]);
    expect(getBookingDates('2026-10-13T17:30:00Z')[0]).toBe('2026-10-15');
    expect(getBookingDates('2026-12-31T23:30:00+07:00').at(-1)).toBe('2027-01-07');
  });

  it('performs calendar arithmetic across leap days and year boundaries', () => {
    expect(addCalendarDays('2028-02-28', 1)).toBe('2028-02-29');
    expect(addCalendarDays('2026-02-28', 1)).toBe('2026-03-01');
    expect(addCalendarDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addCalendarDays('2027-01-01', -1)).toBe('2026-12-31');
  });

  it.each(['2026-02-29', '2026-02-30', '2026-13-01', '14/10/2026', '2026-1-01'])(
    'rejects invalid calendar date %s',
    (date) => {
      expect(isBusinessDate(date)).toBe(false);
      expect(() => addCalendarDays(date, 1)).toThrow(RangeError);
      expect(() => formatBusinessDate(date)).toThrow(RangeError);
    },
  );

  it.each(['2026-10-13', '2026-10-13T09:00:00', '2026-02-30T09:00:00Z', '2026-10-13T99:00:00Z'])(
    'rejects invalid or offset-free instant %s',
    (instant) => {
      expect(() => parseInstant(instant)).toThrow(RangeError);
    },
  );
});
