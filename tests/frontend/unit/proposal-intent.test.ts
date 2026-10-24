import { describe, expect, it, vi } from 'vitest';
import {
  ProposalIntentStorageError,
  ProposalIntentStore,
  type ProposalOperation,
} from '@/services/api/proposal-intent';

const STAFF = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';
const APPOINTMENT = '33333333-3333-4333-8333-333333333333';
const PROPOSAL = '44444444-4444-4444-8444-444444444444';
const STORAGE_KEY = `prl:proposal-intent:v1:STAFF:${STAFF}:${APPOINTMENT}`;

function body() {
  return {
    expected_proposal_version: 4,
    expected_pending_proposal_id: PROPOSAL,
    proposed_starts_at: '2026-10-28T09:00:00.123456+07:00',
    proposed_location: '  Phòng B204  ',
  };
}

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    values,
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      values.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      values.delete(key);
    }),
  };
}

function staffStore(storage = memoryStorage()) {
  const store = new ProposalIntentStore(storage);
  store.bindAccount(STAFF, 'STAFF');
  return { store, storage };
}

describe('proposal operation persistence and replay identity', () => {
  it('persists and freezes exact body, key, and preconditions before submission can begin', () => {
    const { store, storage } = staffStore();
    const input = body();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, input);
    input.expected_proposal_version = 99;
    input.proposed_location = 'Thay đổi ngoài ý định đã xác nhận';

    expect(saved.phase).toBe('ready');
    expect(saved.operation.kind).toBe('create');
    if (saved.operation.kind !== 'create') throw new Error('Expected create');
    expect(saved.operation.body).toEqual(body());
    expect(saved.operation.key).toBe(saved.operation.id);
    expect(Object.isFrozen(saved.operation)).toBe(true);
    expect(Object.isFrozen(saved.operation.body)).toBe(true);
    expect(JSON.parse(storage.values.get(STORAGE_KEY)!)).toMatchObject({
      operation: { key: saved.operation.key, body: body() },
      phase: 'ready',
    });
    expect(store.begin(saved.operation)).toEqual(expect.any(Number));
    expect(JSON.parse(storage.values.get(STORAGE_KEY)!).phase).toBe('submitting');
  });

  it.each(['ready', 'submitting', 'unknown'] as const)(
    'recovers a %s intention after reload with the same original identity',
    (phase) => {
      const { store, storage } = staffStore();
      const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
      if (phase !== 'ready') {
        const version = store.begin(saved.operation)!;
        if (phase === 'unknown') store.markUnknown(saved.operation, version);
      }
      const reloaded = staffStore(storage).store;
      const recovered = reloaded.get(STAFF, 'STAFF', APPOINTMENT)!;

      expect(recovered.phase).toBe('unknown');
      expect(recovered.operation).toEqual(saved.operation);
      expect(recovered.version).toBeGreaterThan(saved.version);
      const version = reloaded.begin(recovered.operation)!;
      expect(version).toBeGreaterThan(recovered.version);
      expect(reloaded.get(STAFF, 'STAFF', APPOINTMENT)?.operation).toEqual(saved.operation);
    },
  );

  it('prevents a second submission and ignores stale callback versions after an explicit retry', () => {
    const { store } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    const first = store.begin(saved.operation)!;
    expect(store.begin(saved.operation)).toBeNull();
    store.markUnknown(saved.operation, first);
    const retry = store.begin(saved.operation)!;

    expect(store.confirm(saved.operation, first)).toBe(false);
    expect(store.clear(saved.operation, first)).toBe(false);
    expect(store.markUnknown(saved.operation, first)).toBe(false);
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('submitting');
    expect(store.confirm(saved.operation, retry)).toBe(true);
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('confirmed');
  });

  it('refuses to overwrite unresolved intent or start the same key with altered preconditions', () => {
    const { store } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    expect(() =>
      store.prepareCreate(STAFF, APPOINTMENT, { ...body(), proposed_location: null }),
    ).toThrow(/khôi phục/);
    if (saved.operation.kind !== 'create') throw new Error('Expected create');
    const altered: ProposalOperation = {
      ...saved.operation,
      body: { ...saved.operation.body, expected_proposal_version: 5 },
    };
    expect(store.begin(altered)).toBeNull();
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)?.operation).toEqual(saved.operation);
  });

  it('retains confirmation across reload until the caller verifies history and detail and clears it', () => {
    const { store, storage } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    store.confirm(saved.operation, store.begin(saved.operation)!);
    const reloaded = staffStore(storage).store;
    const recovered = reloaded.get(STAFF, 'STAFF', APPOINTMENT)!;

    expect(recovered.phase).toBe('confirmed');
    expect(reloaded.begin(recovered.operation)).toBeNull();
    expect(reloaded.clear(recovered.operation, recovered.version)).toBe(true);
    expect(storage.values.has(STORAGE_KEY)).toBe(false);
    const next = reloaded.prepareCreate(STAFF, APPOINTMENT, { ...body(), proposed_location: null });
    expect(next.operation.id).not.toBe(saved.operation.id);
  });

  it('keeps an idempotency conflict blocked until explicit current-state review', () => {
    const { store } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    store.markConflict(saved.operation, store.begin(saved.operation)!);
    expect(store.begin(saved.operation)).toBeNull();
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('conflict');
  });

  it('persists a student decision only for the same proposal and action across reload', () => {
    const storage = memoryStorage();
    const store = new ProposalIntentStore(storage);
    store.bindAccount(OTHER, 'STUDENT');
    const saved = store.prepareDecision(OTHER, APPOINTMENT, PROPOSAL, 'decline');
    store.begin(saved.operation);

    const reloaded = new ProposalIntentStore(storage);
    reloaded.bindAccount(OTHER, 'STUDENT');
    expect(reloaded.get(OTHER, 'STUDENT', APPOINTMENT)).toMatchObject({
      phase: 'unknown',
      operation: {
        id: saved.operation.id,
        proposalId: PROPOSAL,
        action: 'decline',
      },
    });
    expect(() => reloaded.prepareDecision(OTHER, APPOINTMENT, PROPOSAL, 'accept')).toThrow();
    const serialized = [...storage.values.values()].join('');
    expect(serialized).not.toMatch(/password|csrf|description|session|cookie/i);
  });

  it('hides old account recovery and invalidates old callbacks on logout or role/account change', () => {
    const { store } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    const version = store.begin(saved.operation)!;
    store.suspend();

    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
    expect(store.confirm(saved.operation, version)).toBe(false);
    store.bindAccount(OTHER, 'STUDENT');
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
    expect(store.get(OTHER, 'STUDENT', APPOINTMENT)).toBeNull();
    expect(() => store.prepareCreate(STAFF, APPOINTMENT, body())).toThrow(/Tài khoản/);
    store.bindAccount(STAFF, 'STUDENT');
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
    store.bindAccount(STAFF, 'STAFF');
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toMatchObject({
      phase: 'unknown',
      operation: saved.operation,
    });
    expect(store.confirm(saved.operation, version)).toBe(false);
  });

  it('keeps independent tab storage and appointment operations separate', () => {
    const a = staffStore();
    const b = staffStore();
    const first = a.store.prepareCreate(STAFF, APPOINTMENT, body());
    const second = b.store.prepareCreate(STAFF, APPOINTMENT, {
      ...body(),
      proposed_location: null,
    });
    expect(first.operation.id).not.toBe(second.operation.id);
    expect(a.store.get(STAFF, 'STAFF', APPOINTMENT)?.operation).toEqual(first.operation);
    expect(b.store.get(STAFF, 'STAFF', APPOINTMENT)?.operation).toEqual(second.operation);
    const otherAppointment = '55555555-5555-4555-8555-555555555555';
    const third = a.store.prepareCreate(STAFF, otherAppointment, body());
    expect(a.store.get(STAFF, 'STAFF', APPOINTMENT)?.operation).toEqual(first.operation);
    expect(a.store.get(STAFF, 'STAFF', otherAppointment)?.operation).toEqual(third.operation);
  });
});

describe('proposal recovery storage safety', () => {
  it('fails before a new intent exists when browser storage is unavailable', () => {
    const { store, storage } = staffStore();
    storage.setItem.mockImplementation(() => {
      throw new DOMException('Quota exceeded');
    });
    expect(() => store.prepareCreate(STAFF, APPOINTMENT, body())).toThrow(
      ProposalIntentStorageError,
    );
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
    expect(store.getIssue(STAFF, 'STAFF', APPOINTMENT)).toMatch(/Không thể lưu hoặc đọc/);
  });

  it('refuses dispatch if a previously prepared intent can no longer be saved', () => {
    const { store, storage } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    storage.setItem.mockImplementation(() => {
      throw new Error('Blocked storage');
    });
    expect(() => store.begin(saved.operation)).toThrow(ProposalIntentStorageError);
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('ready');
  });

  it('detects a storage implementation that discards writes', () => {
    const { store, storage } = staffStore();
    storage.setItem.mockImplementation(() => undefined);
    expect(() => store.prepareCreate(STAFF, APPOINTMENT, body())).toThrow(
      ProposalIntentStorageError,
    );
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
  });

  it('preserves the live recovery entry if deletion fails', () => {
    const { store, storage } = staffStore();
    const saved = store.prepareCreate(STAFF, APPOINTMENT, body());
    storage.removeItem.mockImplementation(() => {
      throw new Error('Blocked storage');
    });
    expect(() => store.clear(saved.operation)).toThrow(ProposalIntentStorageError);
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)?.operation).toEqual(saved.operation);
  });

  it.each([
    'not-json',
    JSON.stringify({ schema: 1, phase: 'unknown' }),
    JSON.stringify({ schema: 99, phase: 'unknown' }),
  ])('does not silently replace damaged recovery data: %s', (raw) => {
    const storage = memoryStorage();
    storage.values.set(STORAGE_KEY, raw);
    const { store } = staffStore(storage);
    expect(store.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
    expect(store.getIssue(STAFF, 'STAFF', APPOINTMENT)).toMatch(/Không thể lưu hoặc đọc/);
    expect(() => store.prepareCreate(STAFF, APPOINTMENT, body())).toThrow(
      ProposalIntentStorageError,
    );
    expect(storage.values.get(STORAGE_KEY)).toBe(raw);
    expect(store.discardInvalid(STAFF, 'STAFF', APPOINTMENT)).toBe(true);
    expect(store.prepareCreate(STAFF, APPOINTMENT, body()).phase).toBe('ready');
  });

  it.each(['owner', 'location', 'description', 'version'])(
    'rejects tampered persisted %s without exposing it as a recoverable operation',
    (field) => {
      const { store, storage } = staffStore();
      store.prepareCreate(STAFF, APPOINTMENT, body());
      const value = JSON.parse(storage.values.get(STORAGE_KEY)!);
      if (field === 'owner') value.operation.userId = OTHER;
      if (field === 'location') value.operation.body.proposed_location = '  ';
      if (field === 'description') value.operation.body.description = 'Không được lưu';
      if (field === 'version') value.operation.body.expected_proposal_version = '4';
      storage.values.set(STORAGE_KEY, JSON.stringify(value));
      const reloaded = staffStore(storage).store;
      expect(reloaded.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull();
      expect(reloaded.getIssue(STAFF, 'STAFF', APPOINTMENT)).not.toBeNull();
    },
  );
});
