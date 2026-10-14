'use client';

import { useEffect, useState } from 'react';

import { isRequestCancelled } from '@/services';

type ResourceState<T> =
  { key: string; status: 'success'; data: T } | { key: string; status: 'error'; message: string };

export function useDemoResource<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<ResourceState<T> | null>(null);
  const requestKey = `${key}:${attempt}`;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    load(controller.signal).then(
      (data) => {
        if (active) setResult({ key: requestKey, status: 'success', data });
      },
      (error: unknown) => {
        if (active && !isRequestCancelled(error)) {
          setResult({
            key: requestKey,
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
  }, [load, requestKey]);

  return {
    state: result?.key === requestKey ? result : { status: 'loading' as const },
    retry: () => setAttempt((value) => value + 1),
  };
}
