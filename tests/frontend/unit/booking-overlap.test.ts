// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { createMockServices, type MockBookingServices } from '@/services';

async function selectCandidate(
  services: MockBookingServices,
  purposeId: string,
  staffId: string,
  time: string,
  day = 0,
) {
  const date = services.getConfiguration().bookingDates[day];
  const slots = await services.listAvailableSlots({ purposeId, staffId, date });
  const slot = slots.find(
    (item) => Date.parse(item.startsAt) === Date.parse(`${date}T${time}:00+07:00`),
  );
  if (!slot) throw new Error(`Missing candidate slot at ${date} ${time}`);
  return {
    studentId: services.getDemoStudent().id,
    purposeId,
    staffId,
    date,
    slotId: slot.id,
    description: '',
  };
}

describe('student booking overlaps within a demo session', () => {
  it.each([
    ['purpose-thesis', 'staff-demo-02', '09:00', 'purpose-academic', 'staff-demo-04', '09:00'],
    ['purpose-career', 'staff-demo-01', '09:00', 'purpose-career', 'staff-demo-02', '09:15'],
    ['purpose-academic', 'staff-demo-04', '09:15', 'purpose-career', 'staff-demo-01', '09:00'],
    ['purpose-career', 'staff-demo-01', '09:15', 'purpose-thesis', 'staff-demo-03', '09:00'],
    ['purpose-career', 'staff-demo-02', '09:00', 'purpose-thesis', 'staff-demo-02', '09:15'],
  ])(
    'hides and rejects %s/%s/%s overlapping %s/%s/%s, including cached drafts',
    async (purpose, staff, time, nextPurpose, nextStaff, nextTime) => {
      const services = createMockServices({ delayMs: 0 });
      const first = await selectCandidate(services, purpose, staff, time);
      const cached = await selectCandidate(services, nextPurpose, nextStaff, nextTime);
      await services.createBooking(first);

      expect((await services.listAvailableSlots(cached)).map((slot) => slot.id)).not.toContain(
        cached.slotId,
      );
      await expect(services.createBooking(cached)).rejects.toMatchObject({
        code: 'BOOKING_CONFLICT',
        message: expect.stringContaining('trùng với lịch bạn đã đặt'),
      });
      const nextDay = await selectCandidate(services, nextPurpose, nextStaff, '10:00', 1);
      expect((await services.createBooking(nextDay)).reference).toBe('DEMO-0002');
      services.reset();
      expect((await services.listAvailableSlots(cached)).map((slot) => slot.id)).toContain(
        cached.slotId,
      );
    },
  );

  it.each([
    ['09:00', '09:30'],
    ['09:30', '09:00'],
  ])('allows adjacent appointments at %s and %s', async (firstTime, nextTime) => {
    const services = createMockServices({ delayMs: 0 });
    await services.createBooking(
      await selectCandidate(services, 'purpose-career', 'staff-demo-01', firstTime),
    );
    const next = await selectCandidate(services, 'purpose-academic', 'staff-demo-04', nextTime);
    expect((await services.createBooking(next)).reference).toBe('DEMO-0002');
  });

  it('rechecks overlap when concurrent submissions settle, accepting only one', async () => {
    const services = createMockServices({ delayMs: 0 });
    const first = await selectCandidate(services, 'purpose-thesis', 'staff-demo-02', '09:00');
    const second = await selectCandidate(services, 'purpose-academic', 'staff-demo-04', '09:15');
    const results = await Promise.allSettled([
      services.createBooking(first),
      services.createBooking(second),
    ]);
    expect(results.filter((result) => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.find((result) => result.status === 'rejected')).toMatchObject({
      reason: { code: 'BOOKING_CONFLICT' },
    });
  });

  it('keeps reserved times independent of the returned result object', async () => {
    const services = createMockServices({ delayMs: 0 });
    const first = await selectCandidate(services, 'purpose-thesis', 'staff-demo-02', '09:00');
    const next = await selectCandidate(services, 'purpose-academic', 'staff-demo-04', '09:00');
    const result = await services.createBooking(first);
    Object.assign(result.slot, {
      startsAt: '2030-01-01T00:00:00Z',
      endsAt: '2030-01-01T01:00:00Z',
    });
    await expect(services.createBooking(next)).rejects.toMatchObject({ code: 'BOOKING_CONFLICT' });
  });
});
