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
  private controller: AbortController | null = null;
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
    this.controller?.abort();
    this.controller = null;
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
    const intent = this.current;
    if (!intent || intent.userId !== userId) {
      this.clear();
      return Promise.reject(new ApiError('ACCOUNT_CHANGED'));
    }
    if (this.running) return this.running;
    if (intent.receipt) return Promise.resolve(intent.receipt);
    if (intent.phase === 'conflict')
      return Promise.reject(new ApiError('IDEMPOTENCY_CONFLICT', 409));
    this.update({ ...intent, phase: 'submitting' });
    const version = this.version;
    const controller = new AbortController();
    this.controller = controller;
    const task = (async () => {
      try {
        const receipt = await services.create(intent.body, intent.key, userId, {
          signal: controller.signal,
        });
        if (this.version !== version || this.current?.key !== intent.key)
          throw new DOMException('Cancelled', 'AbortError');
        this.update({ ...intent, phase: 'confirmed', receipt });
        return receipt;
      } catch (error) {
        if (this.version === version && this.current?.key === intent.key) {
          if (error instanceof ApiError && error.code === 'ACCOUNT_CHANGED') {
            this.clear();
            services.clearSession();
            services.invalidateSession();
          } else if (error instanceof ApiError && error.code === 'IDEMPOTENCY_CONFLICT')
            this.update({ ...intent, phase: 'conflict' });
          else if (error instanceof ApiError && !error.unknownOutcome) this.update(null);
          else this.update({ ...intent, phase: 'unknown' });
        }
        throw error;
      } finally {
        if (this.version === version) {
          this.running = null;
          this.controller = null;
        }
      }
    })();
    this.running = task;
    return task;
  }
  startNew() {
    if (this.current?.phase === 'confirmed') this.update(null);
  }
}
