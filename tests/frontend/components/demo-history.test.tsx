import { act, renderHook, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { DemoProvider, useDemo } from '@/features/demo/demo-provider';
import { BookingResultView } from '@/features/booking/booking-result';
import { createMockServices, type MockBookingServices } from '@/services';

async function bookNext(services: MockBookingServices) {
  const purpose = (await services.listPurposes())[0];
  const staff = (await services.listStaff(purpose.id))[0];
  const date = services.getConfiguration().bookingDates[0];
  const slot = (
    await services.listAvailableSlots({ purposeId: purpose.id, staffId: staff.id, date })
  )[0];
  return services.createBooking({
    studentId: services.getDemoStudent().id,
    purposeId: purpose.id,
    staffId: staff.id,
    date,
    slotId: slot.id,
    description: '',
  });
}

function setup(bookingId?: string) {
  const services = createMockServices({ delayMs: 0 });
  const view = renderHook(useDemo, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <DemoProvider services={services}>
        {children}
        {bookingId !== undefined && <BookingResultView bookingId={bookingId} />}
      </DemoProvider>
    ),
  });
  return { services, ...view };
}

describe('in-memory booking history', () => {
  it('does not substitute the latest result for an unknown booking ID', async () => {
    const { services, result } = setup('unknown-booking');
    const booking = await bookNext(services);
    act(() => result.current.recordBooking(booking));
    expect(result.current.result).toBe(booking);
    expect(
      screen.getByRole('heading', { name: 'Không tìm thấy lịch hẹn trong phiên demo.' }),
    ).toBeVisible();
    expect(screen.queryByText(`Mã lịch hẹn: ${booking.reference}`)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Về danh sách lịch đã đặt' })).toHaveAttribute(
      'href',
      '/appointments',
    );
  });

  it('retains every unique successful booking, newest first, without changing the latest result when an old result repeats', async () => {
    const { services, result } = setup();
    const first = await bookNext(services);
    const second = await bookNext(services);
    act(() => {
      result.current.recordBooking(first);
      result.current.recordBooking(second);
    });
    expect(result.current.bookings).toEqual([second, first]);
    expect(result.current.result).toBe(second);
    act(() => result.current.recordBooking(first));
    expect(result.current.bookings).toEqual([second, first]);
    expect(result.current.result).toBe(second);
  });

  it('clears history and cancels pending submission on demo reset while retaining the demo student', async () => {
    const { services, result } = setup();
    const booking = await bookNext(services);
    const controller = new AbortController();
    act(() => {
      result.current.setStudent(services.getDemoStudent());
      result.current.recordBooking(booking);
      result.current.beginSubmission(controller);
    });
    act(() => result.current.resetDemo());
    expect(controller.signal.aborted).toBe(true);
    expect(result.current.bookings).toEqual([]);
    expect(result.current.result).toBeNull();
    expect(result.current.student).toEqual(services.getDemoStudent());
    expect(result.current.busy).toBe(false);
  });

  it('removes all bookings and the student on logout', async () => {
    const { services, result } = setup();
    const first = await bookNext(services);
    const second = await bookNext(services);
    act(() => {
      result.current.setStudent(services.getDemoStudent());
      result.current.recordBooking(first);
      result.current.recordBooking(second);
    });
    act(() => result.current.logout());
    expect(result.current.bookings).toEqual([]);
    expect(result.current.result).toBeNull();
    expect(result.current.student).toBeNull();
    expect(services.getConfiguration().scenario).toBe('success');
  });
});
