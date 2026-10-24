import { describe, expect, it } from 'vitest';
import { proposalDateTime, proposalTimestamp } from '@/lib/proposal-time';

describe('proposal business timestamps', () => {
  it('preserves unchanged start exactly for a location-only change', () => {
    const original = '2026-10-28T01:15:00.123456Z';
    expect(proposalDateTime(original, 'Asia/Ho_Chi_Minh')).toEqual({
      date: '2026-10-28',
      time: '08:15',
    });
    expect(proposalTimestamp('2026-10-28', '08:15', 'Asia/Ho_Chi_Minh', original)).toBe(original);
  });

  it('uses the server timezone for changed time and ignores the browser timezone', () => {
    expect(proposalTimestamp('2026-10-28', '08:30', 'Asia/Ho_Chi_Minh')).toBe(
      '2026-10-28T08:30:00+07:00',
    );
    expect(proposalTimestamp('2026-01-28', '08:30', 'America/New_York')).toBe(
      '2026-01-28T08:30:00-05:00',
    );
    expect(proposalTimestamp('2026-10-28', '08:30', 'Asia/Kathmandu')).toBe(
      '2026-10-28T08:30:00+05:45',
    );
  });

  it('rejects DST gaps and folds for a new intention', () => {
    expect(() => proposalTimestamp('2026-03-08', '02:30', 'America/New_York')).toThrow(
      /không tồn tại/,
    );
    expect(() => proposalTimestamp('2026-11-01', '01:30', 'America/New_York')).toThrow(/mơ hồ/);
  });

  it('can preserve an existing unambiguous server instant during a local clock fold', () => {
    const original = '2026-11-01T01:30:00.654321-04:00';
    expect(proposalTimestamp('2026-11-01', '01:30', 'America/New_York', original)).toBe(original);
  });

  it('handles a different calendar day in business time', () => {
    expect(proposalDateTime('2026-10-27T18:30:00Z', 'Asia/Ho_Chi_Minh')).toEqual({
      date: '2026-10-28',
      time: '01:30',
    });
    expect(proposalTimestamp('2026-10-29', '01:30', 'Asia/Ho_Chi_Minh')).toBe(
      '2026-10-29T01:30:00+07:00',
    );
  });

  it.each([
    ['2026-02-30', '08:00', 'Asia/Ho_Chi_Minh'],
    ['2026-10-28', '25:00', 'Asia/Ho_Chi_Minh'],
    ['2026-10-28', '08:00:30', 'Asia/Ho_Chi_Minh'],
    ['2026-10-28', '08:00', 'Invalid/Timezone'],
  ])('refuses invalid proposal clock inputs %s %s %s', (date, time, timezone) => {
    expect(() => proposalTimestamp(date, time, timezone)).toThrow();
  });
});
