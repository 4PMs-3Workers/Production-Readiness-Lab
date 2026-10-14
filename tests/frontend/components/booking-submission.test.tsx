import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { BookingFlow } from '@/features/booking/booking-flow';
import { DemoProvider } from '@/features/demo/demo-provider';
import { createMockServices } from '@/services';
import type { BookingResult } from '@/types/booking';

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

describe('booking submission lifecycle', () => {
  it('keeps a new submission locked when an older cancelled request settles late', async () => {
    const user = userEvent.setup();
    const services = createMockServices({ delayMs: 0 });
    const createBooking = services.createBooking.bind(services);
    const previous = Promise.withResolvers<BookingResult>();
    const current = Promise.withResolvers<void>();
    const submit = vi
      .spyOn(services, 'createBooking')
      // Deliberately defer cancellation settlement until after the next wizard has submitted.
      .mockImplementationOnce(() => previous.promise)
      .mockImplementationOnce(async (draft, options) => {
        await current.promise;
        return createBooking(draft, options);
      });
    const view = render(
      <DemoProvider services={services}>
        <BookingFlow />
      </DemoProvider>,
    );

    async function completeDraft() {
      await user.click(await screen.findByRole('radio', { name: /Nghề nghiệp và thực tập/ }));
      await user.click(screen.getByRole('button', { name: 'Tiếp tục' }));
      await user.click(await screen.findByRole('radio', { name: /Cố vấn Demo 01/ }));
      await user.click(screen.getByRole('button', { name: 'Tiếp tục' }));
      await user.click(screen.getByDisplayValue('2026-10-14'));
      await user.click(await screen.findByRole('radio', { name: /09:00 –/ }));
      await user.click(screen.getByRole('button', { name: 'Tiếp tục' }));
    }

    await completeDraft();
    await user.click(screen.getByRole('button', { name: 'Xác nhận đặt lịch' }));
    const previousSignal = submit.mock.calls[0][1]?.signal;
    expect(screen.getByRole('button', { name: 'Đang xác nhận…' })).toBeDisabled();
    view.rerender(
      <DemoProvider services={services}>
        <p>Trang đăng nhập</p>
      </DemoProvider>,
    );
    expect(previousSignal?.aborted).toBe(true);

    view.rerender(
      <DemoProvider services={services}>
        <BookingFlow />
      </DemoProvider>,
    );
    await completeDraft();
    await user.click(screen.getByText('Thiết lập bản demo', { exact: false }));
    await user.click(screen.getByRole('button', { name: 'Xác nhận đặt lịch' }));
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1][1]?.signal?.aborted).toBe(false);

    await act(async () => {
      previous.reject(new DOMException('Cancelled previous submission.', 'AbortError'));
    });
    expect(screen.getByRole('button', { name: 'Đang xác nhận…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Quay lại/ })).toBeDisabled();
    expect(screen.getByLabelText('Kịch bản trải nghiệm')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Đặt lại demo' })).toBeDisabled();
    expect(router.push).not.toHaveBeenCalled();

    await act(async () => {
      current.resolve();
    });
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/booking/result'));
    expect(screen.getByRole('button', { name: 'Xác nhận đặt lịch' })).toBeEnabled();
    expect(screen.getByLabelText('Kịch bản trải nghiệm')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Đặt lại demo' })).toBeEnabled();
  });
});
