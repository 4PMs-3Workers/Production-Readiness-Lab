'use client';

import { useEffect, useMemo, useState } from 'react';

import { isRequestCancelled } from '@/services';

interface ResourceRequest<T> {
  key: string;
  attempt: number;
  load: (signal: AbortSignal) => Promise<T>;
}

type ResourceState<T> =
  | { request: ResourceRequest<T>; status: 'success'; data: T }
  | { request: ResourceRequest<T>; status: 'error'; message: string };

export function useDemoResource<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<ResourceState<T> | null>(null);
  // A → B → A is a new request, even though its query string matches an older result.
  const request = useMemo(() => ({ key, attempt, load }), [key, attempt, load]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    request.load(controller.signal).then(
      (data) => {
        if (active) setResult({ request, status: 'success', data });
      },
      (error: unknown) => {
        if (active && !isRequestCancelled(error)) {
          setResult({
            request,
            status: 'error',
            message:
              error instanceof Error ? error.message : 'Không thể tải dữ liệu. Vui lòng thử lại.',
          });
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [request]);

  return {
    state: result?.request === request ? result : { status: 'loading' as const },
    retry: () => setAttempt((value) => value + 1),
  };
}
