import type { ProposalCreateBody } from '@/services/proposal-contracts';
import { parseInstant } from '@/lib/business-time';
import { ApiError } from './errors';

export type ProposalRole = 'STUDENT' | 'STAFF';
export type ProposalDecision = 'accept' | 'decline';

interface OperationOwner {
  readonly id: string;
  readonly userId: string;
  readonly appointmentId: string;
}

export type ProposalOperation =
  | (OperationOwner & {
      readonly kind: 'create';
      readonly role: 'STAFF';
      readonly key: string;
      readonly body: ProposalCreateBody;
    })
  | (OperationOwner & {
      readonly kind: 'decision';
      readonly role: 'STUDENT';
      readonly proposalId: string;
      readonly action: ProposalDecision;
    });

export interface StoredProposalIntent {
  readonly operation: ProposalOperation;
  readonly phase: 'ready' | 'submitting' | 'unknown' | 'confirmed' | 'conflict';
  readonly version: number;
}

type IntentStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const PREFIX = 'prl:proposal-intent:v1:';
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const PHASES = ['ready', 'submitting', 'unknown', 'confirmed', 'conflict'];
const RECOVERY_MESSAGE =
  'Không thể lưu hoặc đọc thao tác đổi lịch để khôi phục sau khi tải lại. Hãy kiểm tra trạng thái mới nhất và quyền lưu trữ của trình duyệt trước khi gửi.';

export class ProposalIntentStorageError extends Error {
  constructor() {
    super(RECOVERY_MESSAGE);
    this.name = 'ProposalIntentStorageError';
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: string[]) {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

function uuid(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

function validBody(value: unknown): value is ProposalCreateBody {
  if (
    !record(value) ||
    !exactKeys(value, [
      'expected_proposal_version',
      'expected_pending_proposal_id',
      'proposed_starts_at',
      'proposed_location',
    ]) ||
    !Number.isSafeInteger(value.expected_proposal_version) ||
    (value.expected_proposal_version as number) < 0 ||
    !(value.expected_pending_proposal_id === null || uuid(value.expected_pending_proposal_id)) ||
    typeof value.proposed_starts_at !== 'string' ||
    !(
      value.proposed_location === null ||
      (typeof value.proposed_location === 'string' &&
        Array.from(value.proposed_location).length <= 500 &&
        value.proposed_location.trim().length > 0)
    )
  )
    return false;
  try {
    parseInstant(value.proposed_starts_at);
    return true;
  } catch {
    return false;
  }
}

function validOperation(value: unknown): value is ProposalOperation {
  if (!record(value) || !uuid(value.id) || !uuid(value.userId) || !uuid(value.appointmentId))
    return false;
  if (value.kind === 'create')
    return (
      exactKeys(value, ['id', 'userId', 'appointmentId', 'kind', 'role', 'key', 'body']) &&
      value.role === 'STAFF' &&
      uuid(value.key) &&
      value.id === value.key &&
      validBody(value.body)
    );
  return (
    value.kind === 'decision' &&
    exactKeys(value, ['id', 'userId', 'appointmentId', 'kind', 'role', 'proposalId', 'action']) &&
    value.role === 'STUDENT' &&
    uuid(value.proposalId) &&
    (value.action === 'accept' || value.action === 'decline')
  );
}

function immutable(operation: ProposalOperation): ProposalOperation {
  return Object.freeze(
    operation.kind === 'create'
      ? { ...operation, body: Object.freeze({ ...operation.body }) }
      : { ...operation },
  );
}

/** Per-tab recovery material. It contains no credentials or consultation descriptions. */
export class ProposalIntentStore {
  private owner: { userId: string; role: ProposalRole } | null = null;
  private entries = new Map<string, StoredProposalIntent | null>();
  private issues = new Map<string, string>();
  private listeners = new Set<() => void>();
  private revision = 0;
  private nextVersion = 0;

  constructor(private readonly suppliedStorage?: IntentStorage) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = () => this.revision;

  private storage(): IntentStorage {
    try {
      return this.suppliedStorage ?? window.sessionStorage;
    } catch {
      throw new ProposalIntentStorageError();
    }
  }

  private key(userId: string, role: ProposalRole, appointmentId: string) {
    return `${PREFIX}${role}:${userId}:${appointmentId}`;
  }

  private owns(userId: string, role: ProposalRole) {
    return this.owner?.userId === userId && this.owner.role === role;
  }

  private emit() {
    this.revision += 1;
    this.listeners.forEach((listener) => listener());
  }

  private persist(key: string, entry: StoredProposalIntent) {
    try {
      const serialized = JSON.stringify({ schema: 1, ...entry });
      const storage = this.storage();
      storage.setItem(key, serialized);
      if (storage.getItem(key) !== serialized) throw new ProposalIntentStorageError();
    } catch {
      this.issues.set(key, RECOVERY_MESSAGE);
      throw new ProposalIntentStorageError();
    }
    this.issues.delete(key);
  }

  private remove(key: string) {
    try {
      const storage = this.storage();
      storage.removeItem(key);
      if (storage.getItem(key) !== null) throw new ProposalIntentStorageError();
    } catch {
      this.issues.set(key, RECOVERY_MESSAGE);
      throw new ProposalIntentStorageError();
    }
    this.issues.delete(key);
  }

  /** Call only after the session has authenticated this identity. */
  bindAccount(userId: string, role: ProposalRole) {
    if (!uuid(userId)) throw new ApiError('ACCOUNT_CHANGED', 401);
    if (this.owns(userId, role)) return;
    this.suspend();
    this.owner = { userId, role };
    this.emit();
  }

  /** Invalidate pending callbacks while preserving recovery for the same account. */
  suspend() {
    for (const [key, entry] of this.entries) {
      if (entry?.phase !== 'submitting') continue;
      const unresolved = { ...entry, phase: 'unknown' as const, version: ++this.nextVersion };
      try {
        this.persist(key, unresolved);
      } catch {
        // The already persisted submitting record is recovered as unknown on reload.
      }
    }
    this.owner = null;
    this.entries.clear();
    this.issues.clear();
    this.emit();
  }

  get(
    userId: string | null,
    role: ProposalRole,
    appointmentId: string,
  ): StoredProposalIntent | null {
    if (!userId || !this.owns(userId, role)) return null;
    const key = this.key(userId, role, appointmentId);
    if (this.entries.has(key)) return this.entries.get(key) ?? null;
    let entry: StoredProposalIntent | null = null;
    try {
      const raw = this.storage().getItem(key);
      if (raw !== null) {
        const value: unknown = JSON.parse(raw);
        if (
          !record(value) ||
          !exactKeys(value, ['schema', 'operation', 'phase', 'version']) ||
          value.schema !== 1 ||
          !validOperation(value.operation) ||
          value.operation.userId !== userId ||
          value.operation.role !== role ||
          value.operation.appointmentId !== appointmentId ||
          typeof value.phase !== 'string' ||
          !PHASES.includes(value.phase) ||
          !Number.isSafeInteger(value.version) ||
          (value.version as number) < 1
        )
          throw new ProposalIntentStorageError();
        this.nextVersion = Math.max(this.nextVersion, value.version as number);
        entry = Object.freeze({
          operation: immutable(value.operation),
          phase:
            value.phase === 'ready' || value.phase === 'submitting'
              ? 'unknown'
              : (value.phase as StoredProposalIntent['phase']),
          version: ++this.nextVersion,
        });
        this.persist(key, entry);
      }
    } catch {
      this.issues.set(key, RECOVERY_MESSAGE);
    }
    this.entries.set(key, entry);
    return entry;
  }

  getIssue(userId: string | null, role: ProposalRole, appointmentId: string): string | null {
    if (!userId || !this.owns(userId, role)) return null;
    this.get(userId, role, appointmentId);
    return this.issues.get(this.key(userId, role, appointmentId)) ?? null;
  }

  private prepare(operation: ProposalOperation): StoredProposalIntent {
    if (!this.owns(operation.userId, operation.role)) throw new ApiError('ACCOUNT_CHANGED', 401);
    const existing = this.get(operation.userId, operation.role, operation.appointmentId);
    const key = this.key(operation.userId, operation.role, operation.appointmentId);
    if (this.issues.has(key)) throw new ProposalIntentStorageError();
    if (existing)
      throw new Error('Hãy khôi phục thao tác đã gửi trước khi xác nhận một thay đổi mới.');
    if (!validOperation(operation)) throw new ApiError('VALIDATION_ERROR', 422);
    const entry = Object.freeze({
      operation: immutable(operation),
      phase: 'ready' as const,
      version: ++this.nextVersion,
    });
    this.persist(key, entry);
    this.entries.set(key, entry);
    this.emit();
    return entry;
  }

  prepareCreate(userId: string, appointmentId: string, body: ProposalCreateBody) {
    const key = crypto.randomUUID();
    return this.prepare({
      id: key,
      kind: 'create',
      role: 'STAFF',
      userId,
      appointmentId,
      key,
      body,
    });
  }

  prepareDecision(
    userId: string,
    appointmentId: string,
    proposalId: string,
    action: ProposalDecision,
  ) {
    return this.prepare({
      id: crypto.randomUUID(),
      kind: 'decision',
      role: 'STUDENT',
      userId,
      appointmentId,
      proposalId,
      action,
    });
  }

  private current(operation: ProposalOperation) {
    if (!this.owns(operation.userId, operation.role)) return null;
    const entry = this.entries.get(
      this.key(operation.userId, operation.role, operation.appointmentId),
    );
    return entry?.operation.id === operation.id &&
      JSON.stringify(entry.operation) === JSON.stringify(operation)
      ? entry
      : null;
  }

  isCurrent(operation: ProposalOperation, version: number) {
    return this.current(operation)?.version === version;
  }

  begin(operation: ProposalOperation): number | null {
    const current = this.current(operation);
    if (!current || !['ready', 'unknown'].includes(current.phase)) return null;
    const entry = Object.freeze({
      ...current,
      phase: 'submitting' as const,
      version: ++this.nextVersion,
    });
    const key = this.key(operation.userId, operation.role, operation.appointmentId);
    this.persist(key, entry);
    this.entries.set(key, entry);
    this.emit();
    return entry.version;
  }

  private transition(
    operation: ProposalOperation,
    version: number,
    phase: 'unknown' | 'confirmed' | 'conflict',
  ) {
    const current = this.current(operation);
    if (!current || current.version !== version || current.phase === phase) return false;
    if (!['submitting', 'unknown'].includes(current.phase)) return false;
    const entry = Object.freeze({ ...current, phase });
    const key = this.key(operation.userId, operation.role, operation.appointmentId);
    this.persist(key, entry);
    this.entries.set(key, entry);
    this.emit();
    return true;
  }

  markUnknown(operation: ProposalOperation, version: number) {
    return this.transition(operation, version, 'unknown');
  }

  confirm(operation: ProposalOperation, version: number) {
    return this.transition(operation, version, 'confirmed');
  }

  markConflict(operation: ProposalOperation, version: number) {
    return this.transition(operation, version, 'conflict');
  }

  clear(operation: ProposalOperation, version?: number) {
    const current = this.current(operation);
    if (!current || (version !== undefined && current.version !== version)) return false;
    const key = this.key(operation.userId, operation.role, operation.appointmentId);
    this.remove(key);
    this.entries.set(key, null);
    this.emit();
    return true;
  }

  /** Explicit recovery only, after an authorized current read and user review. */
  discardInvalid(userId: string, role: ProposalRole, appointmentId: string) {
    if (!this.owns(userId, role)) return false;
    const key = this.key(userId, role, appointmentId);
    if (!this.issues.has(key) || this.entries.get(key)) return false;
    this.remove(key);
    this.entries.set(key, null);
    this.emit();
    return true;
  }
}
