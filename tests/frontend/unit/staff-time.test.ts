import { describe, expect, it } from 'vitest';
import { formatBusinessTime, getBusinessDate } from '@/lib/business-time';
import { localDateTimeToOffset } from '@/lib/staff-time';

describe('staff business time', () => {
  it('formats an instant in the server-provided zone even when the browser zone differs', () => {
    const instant = '2026-10-20T01:00:00Z';

    expect(getBusinessDate(instant, 'Asia/Ho_Chi_Minh')).toBe('2026-10-20');
    expect(formatBusinessTime(instant, 'Asia/Ho_Chi_Minh')).toBe('08:00');
    expect(formatBusinessTime(instant, 'America/Los_Angeles')).toBe('18:00');
  });

  it('converts a local date and time to an offset-bearing instant in the staff timezone', () => {
    expect(localDateTimeToOffset('2026-10-20T08:00', 'Asia/Ho_Chi_Minh')).toBe(
      '2026-10-20T08:00:00+07:00',
    );
  });

  it('rejects daylight-saving gaps and folds instead of silently changing the chosen time', () => {
    expect(() => localDateTimeToOffset('2026-03-08T02:30', 'America/New_York')).toThrow(
      /không tồn tại/i,
    );
    expect(() => localDateTimeToOffset('2026-11-01T01:30', 'America/New_York')).toThrow(/mơ hồ/i);
  });

  it.each(['2026-02-30T08:00', '2026-10-20 08:00', '2026-10-20T25:00'])(
    'rejects invalid local date-time %s',
    (value) => expect(() => localDateTimeToOffset(value, 'Asia/Ho_Chi_Minh')).toThrow(),
  );
});
