import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useDemoResource } from '@/features/booking/use-demo-resource';

describe('booking resource ownership', () => {
  it('reloads A after A → B → A without exposing a previous result or a late B response', async () => {
    const firstA = Promise.withResolvers<string[]>();
    const pendingB = Promise.withResolvers<string[]>();
    const latestA = Promise.withResolvers<string[]>();
    const loadA = vi
      .fn<(signal: AbortSignal) => Promise<string[]>>()
      .mockReturnValueOnce(firstA.promise)
      .mockReturnValueOnce(latestA.promise);
    const loadB = vi
      .fn<(signal: AbortSignal) => Promise<string[]>>()
      .mockReturnValueOnce(pendingB.promise);
    const view = renderHook(({ query, load }) => useDemoResource(query, load), {
      initialProps: { query: 'A', load: loadA },
    });
    await act(async () => firstA.resolve(['previous A']));
    expect(view.result.current.state).toMatchObject({ status: 'success', data: ['previous A'] });

    view.rerender({ query: 'B', load: loadB });
    expect(loadA.mock.calls[0][0].aborted).toBe(true);
    view.rerender({ query: 'A', load: loadA });
    expect(loadB.mock.calls[0][0].aborted).toBe(true);
    expect(loadA).toHaveBeenCalledTimes(2);
    expect(view.result.current.state.status).toBe('loading');

    await act(async () => pendingB.resolve(['late B']));
    expect(view.result.current.state.status).toBe('loading');
    await act(async () => latestA.resolve(['current A']));
    expect(view.result.current.state).toMatchObject({ status: 'success', data: ['current A'] });
  });

  it('hides an old result when a new loader serves the same query key', async () => {
    const next = Promise.withResolvers<string[]>();
    const firstLoader = vi.fn(async () => ['old availability']);
    const nextLoader = vi.fn(() => next.promise);
    const view = renderHook(({ load }) => useDemoResource('slots', load), {
      initialProps: { load: firstLoader as (signal: AbortSignal) => Promise<string[]> },
    });
    await waitFor(() => expect(view.result.current.state.status).toBe('success'));
    view.rerender({ load: nextLoader });
    expect(view.result.current.state.status).toBe('loading');
    await act(async () => next.resolve(['new availability']));
    expect(view.result.current.state).toMatchObject({
      status: 'success',
      data: ['new availability'],
    });
  });

  it('retries the current query and ignores an older failure after the query changes', async () => {
    const old = Promise.withResolvers<string[]>();
    const retry = Promise.withResolvers<string[]>();
    const loadA = vi
      .fn<(signal: AbortSignal) => Promise<string[]>>()
      .mockReturnValueOnce(old.promise);
    const loadB = vi
      .fn<(signal: AbortSignal) => Promise<string[]>>()
      .mockRejectedValueOnce(new Error('Current query failed'))
      .mockReturnValueOnce(retry.promise);
    const view = renderHook(({ query, load }) => useDemoResource(query, load), {
      initialProps: { query: 'A', load: loadA },
    });
    view.rerender({ query: 'B', load: loadB });
    await waitFor(() =>
      expect(view.result.current.state).toMatchObject({
        status: 'error',
        message: 'Current query failed',
      }),
    );
    await act(async () => old.reject(new Error('Late error from A')));
    expect(view.result.current.state).toMatchObject({
      status: 'error',
      message: 'Current query failed',
    });
    act(() => view.result.current.retry());
    expect(view.result.current.state.status).toBe('loading');
    expect(loadA).toHaveBeenCalledTimes(1);
    expect(loadB).toHaveBeenCalledTimes(2);
    await act(async () => retry.resolve(['retried B']));
    expect(view.result.current.state).toMatchObject({ status: 'success', data: ['retried B'] });
  });
});
