'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { parseBookingContext } from '@/services/api/booking-context';
import { errorMessage } from '@/services/api/errors';
import type { BookingContext } from '@/types/api';

type BookingContextState =
  | { status: 'loading'; data?: BookingContext }
  | { status: 'error'; error: unknown; message: string; data?: BookingContext }
  | { status: 'success'; data: BookingContext };

interface RequestResult {
  readonly userId: string;
  readonly revision: number;
  readonly attempt: number;
  readonly state: Exclude<BookingContextState, { status: 'loading' }>;
}

/** Keeps only the current student's last valid context available during an explicit refresh. */
export function useBookingContext(
  userId: string,
  revision: number,
  load: (signal: AbortSignal) => Promise<unknown>,
) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<RequestResult | null>(null);
  const [lastValid, setLastValid] = useState<{
    userId: string;
    revision: number;
    data: BookingContext;
  } | null>(null);
  const requestSequence = useRef(0);

  useEffect(() => {
    if (!userId) return;
    const controller = new AbortController();
    const requestId = ++requestSequence.current;
    void Promise.resolve()
      .then(() => load(controller.signal))
      .then(parseBookingContext)
      .then(
        (data) => {
          if (controller.signal.aborted || requestSequence.current !== requestId) return;
          setLastValid({ userId, revision, data });
          setResult({ userId, revision, attempt, state: { status: 'success', data } });
        },
        (error: unknown) => {
          if (
            controller.signal.aborted ||
            (error instanceof Error && error.name === 'AbortError') ||
            requestSequence.current !== requestId
          )
            return;
          setResult({
            userId,
            revision,
            attempt,
            state: { status: 'error', error, message: errorMessage(error) },
          });
        },
      );
    return () => {
      controller.abort();
      if (requestSequence.current === requestId) requestSequence.current += 1;
    };
  }, [userId, revision, load, attempt]);

  const currentContext =
    lastValid?.userId === userId && lastValid.revision === revision ? lastValid.data : undefined;
  const state: BookingContextState =
    result?.userId === userId && result.revision === revision && result.attempt === attempt
      ? result.state.status === 'error' && currentContext
        ? { ...result.state, data: currentContext }
        : result.state
      : { status: 'loading', ...(currentContext ? { data: currentContext } : {}) };

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { state, retry };
}
