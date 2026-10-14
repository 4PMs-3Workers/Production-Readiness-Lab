// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createMockServices, isRequestCancelled } from '@/services';
import type { BookingDraft } from '@/types/booking';

const query = { purposeId: 'purpose-career', staffId: 'staff-demo-01', date: '2026-10-14' };

async function draftForRequest(): Promise<BookingDraft> {
  const services = createMockServices({ delayMs: 0 });
  const [slot] = await services.listAvailableSlots(query);
  return { ...query, studentId: services.getDemoStudent().id, slotId: slot.id, description: '' };
}

afterEach(() => vi.useRealTimers());

describe('mock request lifecycle', () => {
  it('keeps slow responses pending for the configured delay', async () => {
    vi.useFakeTimers();
    const services = createMockServices({ scenario: 'slow' });
    const completed = vi.fn();
    const request = services.listPurposes().then(completed);
    await vi.advanceTimersByTimeAsync(1499);
    expect(completed).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await request;
    expect(completed).toHaveBeenCalledOnce();
  });

  it('rejects a pre-aborted request without consuming a one-time failure', async () => {
    const services = createMockServices({ scenario: 'purposes-error', delayMs: 0 });
    const controller = new AbortController();
    controller.abort();
    const error = await services
      .listPurposes({ signal: controller.signal })
      .catch((error: unknown) => error);
    expect(isRequestCancelled(error)).toBe(true);
    await expect(services.listPurposes()).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' });
    await expect(services.listPurposes()).resolves.toHaveLength(5);
  });

  it('cancels a delayed request, clears its timer, and preserves the fail-once outcome', async () => {
    vi.useFakeTimers();
    const services = createMockServices({ scenario: 'purposes-error', delayMs: 500 });
    const controller = new AbortController();
    const cancelled = expect(
      services.listPurposes({ signal: controller.signal }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(200);
    controller.abort();
    await cancelled;
    expect(vi.getTimerCount()).toBe(0);
    const failed = expect(services.listPurposes()).rejects.toMatchObject({
      code: 'SERVICE_UNAVAILABLE',
    });
    await vi.advanceTimersByTimeAsync(500);
    await failed;
    const retried = services.listPurposes();
    await vi.advanceTimersByTimeAsync(500);
    expect(await retried).toHaveLength(5);
  });

  it('cancels submission without consuming a slot or returning a successful result', async () => {
    vi.useFakeTimers();
    const draft = await draftForRequest();
    const services = createMockServices({ delayMs: 500 });
    const controller = new AbortController();
    const cancelled = expect(
      services.createBooking(draft, { signal: controller.signal }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await cancelled;
    const booking = services.createBooking(draft);
    await vi.advanceTimersByTimeAsync(500);
    expect((await booking).reference).toBe('DEMO-0001');
  });

  it('reset aborts pending reads and writes before replaying the session', async () => {
    vi.useFakeTimers();
    const draft = await draftForRequest();
    const services = createMockServices({ delayMs: 500 });
    const read = expect(services.listStaff(query.purposeId)).rejects.toMatchObject({
      name: 'AbortError',
    });
    const write = expect(services.createBooking(draft)).rejects.toMatchObject({
      name: 'AbortError',
    });
    services.reset({ delayMs: 0 });
    await Promise.all([read, write]);
    expect(vi.getTimerCount()).toBe(0);
    expect((await services.createBooking(draft)).reference).toBe('DEMO-0001');
  });

  it('snapshots the submitted selection while a response is pending', async () => {
    vi.useFakeTimers();
    const draft = await draftForRequest();
    const mutableDraft = { ...draft };
    const services = createMockServices({ delayMs: 500 });
    const booking = services.createBooking(mutableDraft);
    mutableDraft.slotId = 'changed-after-submit';
    mutableDraft.description = 'changed-after-submit';
    await vi.advanceTimersByTimeAsync(500);
    expect(await booking).toMatchObject({ slot: { id: draft.slotId }, description: null });
  });

  it('reproduces out-of-order staff and slot responses and replays the same order after reset', async () => {
    vi.useFakeTimers();
    const services = createMockServices({ scenario: 'stale-response' });
    for (let replay = 0; replay < 2; replay += 1) {
      const staffOrder: string[] = [];
      const firstStaff = services.listStaff('purpose-career').then(() => staffOrder.push('old'));
      const nextStaff = services.listStaff('purpose-other').then((staff) => {
        staffOrder.push('new');
        expect(staff.map((item) => item.id)).toEqual(['staff-demo-04']);
      });
      const slotOrder: string[] = [];
      const firstSlots = services.listAvailableSlots(query).then(() => slotOrder.push('old'));
      const nextSlots = services
        .listAvailableSlots({ ...query, date: '2026-10-15' })
        .then((slots) => {
          slotOrder.push('new');
          expect(slots.every((slot) => slot.date === '2026-10-15')).toBe(true);
        });
      await vi.advanceTimersByTimeAsync(50);
      expect(staffOrder).toEqual(['new']);
      expect(slotOrder).toEqual(['new']);
      await vi.advanceTimersByTimeAsync(1500);
      await Promise.all([firstStaff, nextStaff, firstSlots, nextSlots]);
      expect(staffOrder).toEqual(['new', 'old']);
      expect(slotOrder).toEqual(['new', 'old']);
      services.reset();
    }
  });
});
