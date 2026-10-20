import { describe, expect, it } from 'vitest';
import { parseBookingContext } from '@/services/api/booking-context';
import { ApiError } from '@/services/api/errors';

const valid = {
  timezone: 'Asia/Ho_Chi_Minh',
  businessDate: '2026-10-18',
  minBookingDate: '2026-10-19',
  maxBookingDate: '2026-10-25',
};

describe('parseBookingContext', () => {
  it('returns a context with a valid IANA timezone and ordered calendar bounds', () => {
    expect(parseBookingContext(valid)).toEqual(valid);
  });

  it.each([
    ['invalid timezone', { ...valid, timezone: 'Mars/Olympus' }],
    ['invalid business date', { ...valid, businessDate: '2026-02-30' }],
    ['invalid lower bound', { ...valid, minBookingDate: 'tomorrow' }],
    ['reversed bounds', { ...valid, minBookingDate: '2026-10-26' }],
    ['missing fields', { timezone: 'UTC' }],
  ])('rejects %s as a service outage', (_reason, value) => {
    try {
      parseBookingContext(value);
      throw new Error('Expected malformed context to throw');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: 'SERVICE_UNAVAILABLE', status: 503 });
    }
  });
});
