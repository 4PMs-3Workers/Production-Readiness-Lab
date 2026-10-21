import type { StaffOutcomeStatus } from '@/services/staff-contracts';

export interface StaffChangeIntent {
  readonly appointmentId: string;
  readonly userId: string;
  readonly status: 'CANCELLED' | StaffOutcomeStatus;
  readonly reason?: string;
}

interface StoredStaffChange {
  readonly change: StaffChangeIntent;
  readonly phase: 'pending' | 'unknown';
  readonly version: number;
}

/** Keeps an in-flight staff write bound to its account and appointment across route changes. */
export class StaffChangeIntentStore {
  private changes = new Map<string, StoredStaffChange>();
  private listeners = new Set<() => void>();
  private nextVersion = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private key(userId: string, appointmentId: string) {
    return `${userId}:${appointmentId}`;
  }

  get(userId: string | null, appointmentId: string): StoredStaffChange | null {
    return userId ? (this.changes.get(this.key(userId, appointmentId)) ?? null) : null;
  }

  begin(change: StaffChangeIntent): number | null {
    const key = this.key(change.userId, change.appointmentId);
    const existing = this.changes.get(key)?.change;
    if (existing && (existing.status !== change.status || existing.reason !== change.reason))
      return null;
    const version = ++this.nextVersion;
    this.changes.set(key, {
      change: existing ?? Object.freeze({ ...change }),
      phase: 'pending',
      version,
    });
    this.emit();
    return version;
  }

  markUnknown(userId: string | null, appointmentId: string, version: number) {
    if (!userId) return;
    const key = this.key(userId, appointmentId);
    const existing = this.changes.get(key);
    if (!existing || existing.version !== version || existing.phase === 'unknown') return;
    this.changes.set(key, { ...existing, phase: 'unknown' });
    this.emit();
  }

  clear(userId: string, appointmentId: string, version: number) {
    const key = this.key(userId, appointmentId);
    if (this.changes.get(key)?.version !== version) return;
    this.changes.delete(key);
    this.emit();
  }

  clearAll() {
    if (!this.changes.size) return;
    this.changes.clear();
    this.emit();
  }

  private emit() {
    this.listeners.forEach((listener) => listener());
  }
}
