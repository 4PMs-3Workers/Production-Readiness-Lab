import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useBookingContext } from '@/features/api/use-booking-context';
import { ApiError } from '@/services/api/errors';

const context = {
  timezone: 'Asia/Ho_Chi_Minh',
  business_date: '2026-10-18',
  min_booking_date: '2026-10-19',
  max_booking_date: '2026-10-25',
};

describe('booking context refresh ownership', () => {
  it('retains a same-student context while refreshing and after refresh failure', async () => {
    const refreshed = Promise.withResolvers<unknown>();
    const load = vi
      .fn<(signal: AbortSignal) => Promise<unknown>>()
      .mockResolvedValueOnce(context)
      .mockReturnValueOnce(refreshed.promise);
    const view = renderHook(() => useBookingContext('student', 0, load));
    await waitFor(() => expect(view.result.current.state.status).toBe('success'));

    act(() => view.result.current.retry());
    expect(view.result.current.state).toMatchObject({ status: 'loading', data: expect.anything() });
    await act(async () => refreshed.reject(new ApiError('SERVICE_UNAVAILABLE', 503)));

    expect(view.result.current.state).toMatchObject({
      status: 'error',
      message: expect.stringContaining('Dịch vụ tạm thời'),
      data: {
        timezone: 'Asia/Ho_Chi_Minh',
        minBookingDate: '2026-10-19',
        maxBookingDate: '2026-10-25',
      },
    });
  });

  it('does not expose a previous student context or a late response after account switch', async () => {
    const previous = Promise.withResolvers<unknown>();
    const next = Promise.withResolvers<unknown>();
    const signals: AbortSignal[] = [];
    const load = vi.fn<(signal: AbortSignal) => Promise<unknown>>((signal) => {
      signals.push(signal);
      return signals.length === 1 ? previous.promise : next.promise;
    });
    const view = renderHook(({ userId, revision }) => useBookingContext(userId, revision, load), {
      initialProps: { userId: 'student', revision: 0 },
    });
    await waitFor(() => expect(signals).toHaveLength(1));

    view.rerender({ userId: 'other-student', revision: 0 });
    expect(signals[0].aborted).toBe(true);
    expect(view.result.current.state).toEqual({ status: 'loading' });
    await act(async () => previous.resolve(context));
    expect(view.result.current.state).toEqual({ status: 'loading' });

    await act(async () => next.resolve({ ...context, timezone: 'UTC' }));
    await waitFor(() => expect(view.result.current.state.status).toBe('success'));
    expect(view.result.current.state).toMatchObject({
      status: 'success',
      data: { timezone: 'UTC' },
    });
  });

  it('does not retain context when the same user starts a new authenticated session', async () => {
    const next = Promise.withResolvers<unknown>();
    let reads = 0;
    const load = vi.fn<(signal: AbortSignal) => Promise<unknown>>(() => {
      reads += 1;
      return reads === 1 ? Promise.resolve(context) : next.promise;
    });
    const view = renderHook(({ revision }) => useBookingContext('student', revision, load), {
      initialProps: { revision: 0 },
    });
    await waitFor(() => expect(view.result.current.state.status).toBe('success'));

    view.rerender({ revision: 1 });
    await waitFor(() => expect(reads).toBe(2));
    expect(view.result.current.state).toEqual({ status: 'loading' });
    await act(async () => next.resolve({ ...context, timezone: 'UTC' }));
    await waitFor(() => expect(view.result.current.state.status).toBe('success'));
    expect(view.result.current.state).toMatchObject({ data: { timezone: 'UTC' } });
  });
});
