'use client';

import { useEffect, useMemo, useState } from 'react';
import { errorMessage } from '@/services/api/errors';

type Result<T> =
  { status: 'success'; data: T } | { status: 'error'; error: unknown; message: string };
export function useApiResource<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ request: object; result: Result<T> } | null>(null);
  const request = useMemo(() => ({ key, load, attempt }), [key, load, attempt]);
  useEffect(() => {
    const controller = new AbortController();
    request.load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ request, result: { status: 'success', data } });
      },
      (error: unknown) => {
        if (!controller.signal.aborted && !(error instanceof Error && error.name === 'AbortError'))
          setResult({ request, result: { status: 'error', error, message: errorMessage(error) } });
      },
    );
    return () => controller.abort();
  }, [request]);
  return {
    state: result?.request === request ? result.result : { status: 'loading' as const },
    retry: () => setAttempt((value) => value + 1),
  };
}
