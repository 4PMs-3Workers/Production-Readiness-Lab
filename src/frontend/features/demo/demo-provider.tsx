'use client';

import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

import { createMockServices, type MockBookingServices } from '@/services';
import type { BookingResult, StudentContext } from '@/types/booking';
import type { DemoScenarioId } from '@/types/demo';

interface DemoContextValue {
  services: MockBookingServices;
  revision: number;
  result: BookingResult | null;
  bookings: readonly BookingResult[];
  recordBooking: (booking: BookingResult) => void;
  student: StudentContext | null;
  setStudent: (student: StudentContext) => void;
  logout: () => void;
  busy: boolean;
  beginSubmission: (controller: AbortController) => void;
  finishSubmission: (controller: AbortController) => void;
  resetDemo: (scenario?: DemoScenarioId) => void;
}

const DemoContext = createContext<DemoContextValue | null>(null);

export function DemoProvider({
  children,
  services: suppliedServices,
}: {
  children: ReactNode;
  services?: MockBookingServices;
}) {
  const [services] = useState(() => suppliedServices ?? createMockServices());
  const [revision, setRevision] = useState(0);
  const [bookings, setBookings] = useState<readonly BookingResult[]>([]);
  const result = bookings[0] ?? null;
  const [student, setStudent] = useState<StudentContext | null>(null);
  const [submission, setSubmission] = useState<AbortController | null>(null);

  const beginSubmission = useCallback((controller: AbortController) => {
    setSubmission(controller);
  }, []);
  const finishSubmission = useCallback((controller: AbortController) => {
    // An older request may settle after navigation or a demo reset starts a new submission.
    setSubmission((current) => (current === controller ? null : current));
  }, []);
  const recordBooking = useCallback((booking: BookingResult) => {
    setBookings((current) =>
      current.some((item) => item.id === booking.id) ? current : [booking, ...current],
    );
  }, []);

  function resetDemo(scenario?: DemoScenarioId) {
    submission?.abort();
    services.reset(scenario ? { scenario } : undefined);
    setBookings([]);
    setSubmission(null);
    setRevision((value) => value + 1);
  }

  function logout() {
    submission?.abort();
    services.reset({ scenario: 'success' });
    setStudent(null);
    setBookings([]);
    setSubmission(null);
    setRevision((value) => value + 1);
  }

  return (
    <DemoContext.Provider
      value={{
        services,
        revision,
        result,
        bookings,
        recordBooking,
        student,
        setStudent,
        logout,
        busy: submission !== null,
        beginSubmission,
        finishSubmission,
        resetDemo,
      }}
    >
      {children}
    </DemoContext.Provider>
  );
}

export function useDemo() {
  const context = useContext(DemoContext);
  if (!context) throw new Error('DemoProvider is required.');
  return context;
}
