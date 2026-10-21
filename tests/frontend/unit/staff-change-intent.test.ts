import { describe, expect, it } from 'vitest';
import { StaffChangeIntentStore } from '@/services/api/staff-change-intent';

describe('staff change intent ownership', () => {
  it('ignores an older continuation after a retry or a new operation begins', () => {
    const store = new StaffChangeIntentStore();
    const change = {
      userId: 'staff-a',
      appointmentId: 'appointment-1',
      status: 'COMPLETED',
    } as const;
    const first = store.begin(change)!;
    store.markUnknown(change.userId, change.appointmentId, first);
    const retry = store.begin(change)!;

    store.markUnknown(change.userId, change.appointmentId, first);
    store.clear(change.userId, change.appointmentId, first);
    expect(store.get(change.userId, change.appointmentId)).toMatchObject({
      phase: 'pending',
      version: retry,
    });

    store.clear(change.userId, change.appointmentId, retry);
    const next = store.begin({ ...change, status: 'NO_SHOW' })!;
    store.markUnknown(change.userId, change.appointmentId, retry);
    store.clear(change.userId, change.appointmentId, retry);
    expect(store.get(change.userId, change.appointmentId)).toMatchObject({
      phase: 'pending',
      version: next,
      change: { status: 'NO_SHOW' },
    });
  });
});
