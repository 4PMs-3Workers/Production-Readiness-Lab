import { describe, expect, it } from 'vitest';
import { StaffChangeIntentStore } from '@/services/api/staff-change-intent';

describe('staff change intent ownership', () => {
  it('retains only the same staff recovery and rejects old-session continuations', () => {
    const store = new StaffChangeIntentStore();
    const change = {
      userId: 'staff-a',
      appointmentId: 'appointment-1',
      status: 'CANCELLED',
      reason: 'Original reason',
    } as const;
    const pending = store.begin(change)!;
    const unknownChange = { ...change, appointmentId: 'appointment-2', status: 'NO_SHOW' } as const;
    const unknown = store.begin(unknownChange)!;
    store.markUnknown('staff-a', 'appointment-2', unknown);
    store.begin({ ...change, userId: 'staff-b' });

    store.suspendForSessionLoss('staff-a');
    for (const [appointmentId, version, original] of [
      ['appointment-1', pending, change],
      ['appointment-2', unknown, unknownChange],
    ] as const) {
      const retained = store.get('staff-a', appointmentId)!;
      expect(retained).toMatchObject({ phase: 'unknown', change: original });
      expect(retained.version).toBeGreaterThan(version);
      store.clear('staff-a', appointmentId, version);
      store.markUnknown('staff-a', appointmentId, version);
      expect(store.get('staff-a', appointmentId)).toBe(retained);
      expect(store.begin({ ...original, status: 'COMPLETED' })).toBeNull();
    }
    expect(store.get('staff-b', 'appointment-1')).toBeNull();
    const retry = store.begin(change)!;
    store.clear('staff-a', 'appointment-1', retry);
    expect(store.get('staff-a', 'appointment-1')).toBeNull();
  });
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
