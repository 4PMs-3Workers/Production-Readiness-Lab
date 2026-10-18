import type { BookingBody, BookingReceipt } from '@/types/api';
import type { ApiServices } from './client';
import { ApiError } from './errors';

export interface BookingIntent {
  readonly userId: string;
  readonly key: string;
  readonly body: BookingBody;
  readonly phase: 'ready' | 'submitting' | 'unknown' | 'confirmed' | 'conflict';
  readonly receipt?: BookingReceipt;
}

/** Lives above route components. Never writes descriptions or credentials to browser storage. */
export class BookingIntentStore {
  private current: BookingIntent | null = null;
  private listeners = new Set<() => void>();
  private running: Promise<BookingReceipt> | null = null;
  private version = 0;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  snapshot = () => this.current;
  private update(value: BookingIntent | null) {
    this.current = value;
    this.listeners.forEach((listener) => listener());
  }
  clear() {
    this.version += 1;
    this.running = null;
    this.update(null);
  }
  prepare(userId: string, body: BookingBody) {
    if (this.current) return this.current;
    const intent: BookingIntent = {
      userId,
      key: crypto.randomUUID(),
      body: Object.freeze({ ...body }),
      phase: 'ready',
    };
    this.update(intent);
    return intent;
  }
  submit(services: ApiServices, userId: string): Promise<BookingReceipt> {
    if (this.running) return this.running;
    const intent = this.current;
    if (!intent || intent.userId !== userId) {
      this.clear();
      return Promise.reject(new ApiError('ACCOUNT_CHANGED'));
    }
    if (intent.receipt) return Promise.resolve(intent.receipt);
    if (intent.phase === 'conflict')
      return Promise.reject(new ApiError('IDEMPOTENCY_CONFLICT', 409));
    this.update({ ...intent, phase: 'submitting' });
    const version = this.version;
    const task = (async () => {
      try {
        const verified = await services.me();
        if (verified.id !== userId || verified.role !== 'STUDENT') {
          this.clear();
          services.clearSession();
          services.invalidateSession();
          throw new ApiError('ACCOUNT_CHANGED', 401);
        }
        if (this.current?.key !== intent.key) throw new DOMException('Cancelled', 'AbortError');
        const receipt = await services.create(intent.body, intent.key);
        if (this.current?.key !== intent.key) throw new DOMException('Cancelled', 'AbortError');
        this.update({ ...intent, phase: 'confirmed', receipt });
        return receipt;
      } catch (error) {
        if (this.current?.key === intent.key) {
          if (error instanceof ApiError && error.code === 'IDEMPOTENCY_CONFLICT')
            this.update({ ...intent, phase: 'conflict' });
          else if (error instanceof ApiError && !error.unknownOutcome) this.update(null);
          else this.update({ ...intent, phase: 'unknown' });
        }
        throw error;
      } finally {
        if (this.version === version) this.running = null;
      }
    })();
    this.running = task;
    return task;
  }
  startNew() {
    if (this.current?.phase === 'confirmed') this.update(null);
  }
}
