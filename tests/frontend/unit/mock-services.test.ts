// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { createMockServices, DemoServiceError } from '@/services';
import type { MockBookingServices } from '@/services';
import type { BookingDraft, PurposeCode } from '@/types/booking';
import type { DemoScenarioId } from '@/types/demo';

const credentials = { email: 'reviewer@example.test', password: 'temporary-demo-input' };

async function selectDraft(services: MockBookingServices, code: PurposeCode = 'CAREER_INTERNSHIP') {
  const student = await services.loginDemo(credentials);
  const purpose = (await services.listPurposes()).find((item) => item.code === code)!;
  const staff = (await services.listStaff(purpose.id))[0];
  const date = services.getConfiguration().bookingDates[0];
  const [slot] = await services.listAvailableSlots({
    purposeId: purpose.id,
    staffId: staff.id,
    date,
  });
  return {
    studentId: student.id,
    purposeId: purpose.id,
    staffId: staff.id,
    date,
    slotId: slot.id,
    description: '',
  } satisfies BookingDraft;
}

describe('mock booking services', () => {
  it('provides the five purpose codes, positive durations, and required OTHER text', async () => {
    const services = createMockServices({ delayMs: 0 });
    const purposes = await services.listPurposes();
    expect(purposes.map((purpose) => purpose.code)).toEqual([
      'CAREER_INTERNSHIP',
      'THESIS_RESEARCH',
      'ACADEMIC_ADVISOR',
      'GENERAL_ADVICE',
      'OTHER',
    ]);
    expect(purposes.every((purpose) => purpose.durationMinutes > 0)).toBe(true);
    expect(purposes.find((purpose) => purpose.code === 'OTHER')?.requiresFreeText).toBe(true);
    expect((await services.listStaff('purpose-career')).map((staff) => staff.id)).toEqual([
      'staff-demo-01',
      'staff-demo-02',
    ]);
    expect((await services.listStaff('purpose-other')).map((staff) => staff.id)).toEqual([
      'staff-demo-04',
    ]);
  });

  it('offers consistent declared slots for every associated staff member on D+1 through D+7', async () => {
    const services = createMockServices({ delayMs: 0 });
    const dates = services.getConfiguration().bookingDates;
    const staffIds = new Set<string>();
    const slotIds = new Set<string>();
    const locations = new Set<string | null>();
    for (const purpose of await services.listPurposes()) {
      for (const staff of await services.listStaff(purpose.id)) {
        staffIds.add(staff.id);
        for (const date of dates) {
          const slots = await services.listAvailableSlots({
            purposeId: purpose.id,
            staffId: staff.id,
            date,
          });
          expect(slots.length).toBeGreaterThan(0);
          for (const slot of slots) {
            expect(slot).toMatchObject({ purposeId: purpose.id, staffId: staff.id, date });
            expect(slot.startsAt).toMatch(/(?:Z|[+-]\d{2}:\d{2})$/);
            expect(slot.endsAt).toMatch(/(?:Z|[+-]\d{2}:\d{2})$/);
            expect(new Date(slot.startsAt).getUTCMinutes() % 15).toBe(0);
            expect((Date.parse(slot.endsAt) - Date.parse(slot.startsAt)) / 60_000).toBe(
              purpose.durationMinutes,
            );
            expect(slotIds.has(slot.id)).toBe(false);
            slotIds.add(slot.id);
            locations.add(slot.location);
          }
        }
      }
    }
    expect(staffIds.size).toBe(4);
    expect(locations.has(null)).toBe(true);
    expect([...locations].some((location) => typeof location === 'string')).toBe(true);
    for (const date of ['2026-10-13', '2026-10-21']) {
      expect(
        await services.listAvailableSlots({
          purposeId: 'purpose-career',
          staffId: 'staff-demo-01',
          date,
        }),
      ).toEqual([]);
    }
  });

  it('returns fake identity without reflecting or retaining login credentials', async () => {
    const services = createMockServices({ delayMs: 0 });
    const student = await services.loginDemo(credentials);
    expect(student).toEqual({
      id: 'student-demo-01',
      displayName: 'Sinh viên Demo HUS',
      email: 'student@example.test',
    });
    expect(JSON.stringify(student)).not.toContain(credentials.password);
    expect(JSON.stringify(services.getConfiguration())).not.toContain(credentials.email);
    await expect(services.loginDemo({ ...credentials, password: '' })).rejects.toMatchObject({
      code: 'INVALID_CREDENTIALS',
    });
    await expect(services.loginDemo({ ...credentials, email: 'invalid' })).rejects.toMatchObject({
      code: 'INVALID_CREDENTIALS',
    });
  });

  it.each([
    ['invalid-login', 'INVALID_CREDENTIALS'],
    ['login-error', 'SERVICE_UNAVAILABLE'],
  ] as const)('reproduces %s without returning a student', async (scenario, code) => {
    const services = createMockServices({ scenario, delayMs: 0 });
    await expect(services.loginDemo(credentials)).rejects.toMatchObject({
      name: 'DemoServiceError',
      code,
    });
    await expect(services.loginDemo(credentials)).rejects.toMatchObject({ code });
  });

  it.each(['empty-purposes', 'empty-staff', 'empty-slots'] as const)(
    'reproduces %s',
    async (scenario) => {
      const services = createMockServices({ scenario, delayMs: 0 });
      const result =
        scenario === 'empty-purposes'
          ? await services.listPurposes()
          : scenario === 'empty-staff'
            ? await services.listStaff('purpose-career')
            : await services.listAvailableSlots({
                purposeId: 'purpose-career',
                staffId: 'staff-demo-01',
                date: '2026-10-14',
              });
      expect(result).toEqual([]);
    },
  );

  it.each(['purposes-error', 'staff-error', 'slots-error'] as const)(
    'fails once, retries successfully, and replays %s after reset',
    async (scenario) => {
      const services = createMockServices({ scenario, delayMs: 0 });
      const load = () =>
        scenario === 'purposes-error'
          ? services.listPurposes()
          : scenario === 'staff-error'
            ? services.listStaff('purpose-career')
            : services.listAvailableSlots({
                purposeId: 'purpose-career',
                staffId: 'staff-demo-01',
                date: '2026-10-14',
              });
      await expect(load()).rejects.toBeInstanceOf(DemoServiceError);
      await expect(load()).resolves.not.toHaveLength(0);
      services.reset();
      await expect(load()).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' });
    },
  );

  it('creates a deterministic result only after successful submission, including null location', async () => {
    const services = createMockServices({ delayMs: 0 });
    const draft = await selectDraft(services);
    const slots = await services.listAvailableSlots(draft);
    const result = await services.createBooking({ ...draft, slotId: slots[1].id });
    expect(result).toMatchObject({
      reference: 'DEMO-0001',
      isDemo: true,
      description: null,
      createdAt: '2026-10-13T09:00:00+07:00',
      slot: { id: slots[1].id, location: null },
    });
    expect(result.purpose.durationMinutes).toBe(30);
    expect(result.staff.id).toBe(draft.staffId);
    const remaining = await services.listAvailableSlots(draft);
    expect(remaining.map((slot) => slot.id)).not.toContain(slots[1].id);
    await expect(services.createBooking({ ...draft, slotId: slots[1].id })).rejects.toMatchObject({
      code: 'BOOKING_CONFLICT',
    });
    services.reset();
    expect(await services.createBooking({ ...draft, slotId: slots[1].id })).toEqual(result);
  });

  it('validates required OTHER text before creating a result', async () => {
    const services = createMockServices({ delayMs: 0 });
    const draft = await selectDraft(services, 'OTHER');
    await expect(services.createBooking({ ...draft, description: '  \n ' })).rejects.toMatchObject({
      code: 'INVALID_REQUEST',
    });
    const result = await services.createBooking({
      ...draft,
      description: '  Cần trao đổi về kế hoạch học tập.  ',
    });
    expect(result.description).toBe('Cần trao đổi về kế hoạch học tập.');
    expect(result.reference).toBe('DEMO-0001');
  });

  it('keeps availability and draft intact when submission fails', async () => {
    const services = createMockServices({ scenario: 'booking-error', delayMs: 0 });
    const draft = await selectDraft(services);
    const before = { ...draft };
    await expect(services.createBooking(draft)).rejects.toMatchObject({
      code: 'SERVICE_UNAVAILABLE',
    });
    await expect(services.createBooking(draft)).rejects.toMatchObject({
      code: 'SERVICE_UNAVAILABLE',
    });
    expect(draft).toEqual(before);
    expect((await services.listAvailableSlots(draft)).map((slot) => slot.id)).toContain(
      draft.slotId,
    );
    services.reset({ delayMs: 0 });
    expect((await services.createBooking(draft)).reference).toBe('DEMO-0001');
  });

  it('removes the conflicting slot, accepts another slot, and replays the conflict after reset', async () => {
    const services = createMockServices({ scenario: 'conflict-once', delayMs: 0 });
    const draft = await selectDraft(services);
    await expect(services.createBooking(draft)).rejects.toMatchObject({ code: 'BOOKING_CONFLICT' });
    const remaining = await services.listAvailableSlots(draft);
    expect(remaining.map((slot) => slot.id)).not.toContain(draft.slotId);
    expect(remaining.length).toBeGreaterThan(0);
    expect((await services.createBooking({ ...draft, slotId: remaining[0].id })).reference).toBe(
      'DEMO-0001',
    );
    services.reset();
    await expect(services.createBooking(draft)).rejects.toMatchObject({ code: 'BOOKING_CONFLICT' });
  });

  it('rejects unknown IDs, mismatched selections, and invalid dates without returning a result', async () => {
    const services = createMockServices({ delayMs: 0 });
    const draft = await selectDraft(services);
    await expect(services.listStaff('unknown')).rejects.toMatchObject({ code: 'INVALID_REQUEST' });
    await expect(
      services.listAvailableSlots({ ...draft, staffId: 'staff-demo-04' }),
    ).rejects.toMatchObject({ code: 'INVALID_REQUEST' });
    await expect(
      services.listAvailableSlots({ ...draft, date: '2026-02-30' }),
    ).rejects.toMatchObject({ code: 'INVALID_REQUEST' });
    for (const change of [
      { studentId: 'unknown' },
      { slotId: 'unknown' },
      { date: '2026-10-15' },
      { purposeId: 'purpose-academic' },
    ]) {
      await expect(services.createBooking({ ...draft, ...change })).rejects.toMatchObject({
        code: 'INVALID_REQUEST',
      });
    }
    expect((await services.createBooking(draft)).reference).toBe('DEMO-0001');
  });

  it('isolates instances and returns copies of catalog, identity, slot and result data', async () => {
    const first = createMockServices({ delayMs: 0 });
    const second = createMockServices({ delayMs: 0 });
    const purposes = await first.listPurposes();
    Object.assign(purposes[0], { name: 'Changed externally' });
    purposes.pop();
    expect((await first.listPurposes())[0].name).toBe('Nghề nghiệp và thực tập');
    expect(await first.listPurposes()).toHaveLength(5);
    Object.assign(await first.loginDemo(credentials), { id: 'changed' });
    const draft = await selectDraft(first);
    Object.assign((await first.listAvailableSlots(draft))[0], { location: 'Changed externally' });
    const result = await first.createBooking(draft);
    expect(result.slot.location).toBe('Phòng tư vấn demo A101');
    Object.assign(result.purpose, { name: 'Changed externally' });
    expect((await first.listPurposes())[0].name).toBe('Nghề nghiệp và thực tập');
    Object.assign(result.slot, { location: 'Changed externally' });
    expect((await second.listAvailableSlots(draft)).map((slot) => slot.id)).toContain(draft.slotId);
    first.reset();
    expect((await first.createBooking(draft)).slot.location).toBe('Phòng tư vấn demo A101');
    expect((await second.createBooking(draft)).reference).toBe('DEMO-0001');
  });

  it('moves fixtures with an explicit clock and rejects invalid configuration without replacing a working session', async () => {
    const services = createMockServices({ now: '2026-12-31T17:30:00Z', delayMs: 0 });
    expect(services.getConfiguration().bookingDates[0]).toBe('2027-01-02');
    const draft = await selectDraft(services);
    expect((await services.createBooking(draft)).slot.date).toBe('2027-01-02');
    expect(() => services.reset({ now: 'invalid' })).toThrow(RangeError);
    expect(services.getConfiguration().now).toBe('2026-12-31T17:30:00Z');
    for (const delayMs of [-1, Infinity, NaN, 0.5, 30_001]) {
      expect(() => createMockServices({ delayMs })).toThrow(RangeError);
    }
  });

  it('rejects unknown runtime scenarios, including inherited object keys', () => {
    for (const scenario of ['unknown', '__proto__', 'toString']) {
      expect(() => createMockServices({ scenario: scenario as DemoScenarioId })).toThrow(
        RangeError,
      );
    }
  });
});
