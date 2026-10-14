'use client';

import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

import { createMockServices, type MockBookingServices } from '@/services';
import type { BookingResult } from '@/types/booking';
import type { DemoScenarioId } from '@/types/demo';

interface DemoContextValue {
  services: MockBookingServices;
  revision: number;
  result: BookingResult | null;
  setResult: (result: BookingResult | null) => void;
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
  const [result, setResult] = useState<BookingResult | null>(null);
  const [submission, setSubmission] = useState<AbortController | null>(null);

  const beginSubmission = useCallback((controller: AbortController) => {
    setSubmission(controller);
  }, []);
  const finishSubmission = useCallback((controller: AbortController) => {
    // An older request may settle after navigation or a demo reset starts a new submission.
    setSubmission((current) => (current === controller ? null : current));
  }, []);

  function resetDemo(scenario?: DemoScenarioId) {
    services.reset(scenario ? { scenario } : undefined);
    setResult(null);
    setSubmission(null);
    setRevision((value) => value + 1);
  }

  return (
    <DemoContext.Provider
      value={{
        services,
        revision,
        result,
        setResult,
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
