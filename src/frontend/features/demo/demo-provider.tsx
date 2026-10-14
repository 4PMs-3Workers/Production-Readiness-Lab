'use client';

import { createContext, useContext, useState, type ReactNode } from 'react';

import { createMockServices, type MockBookingServices } from '@/services';
import type { BookingResult } from '@/types/booking';
import type { DemoScenarioId } from '@/types/demo';

interface DemoContextValue {
  services: MockBookingServices;
  revision: number;
  result: BookingResult | null;
  setResult: (result: BookingResult | null) => void;
  busy: boolean;
  setBusy: (busy: boolean) => void;
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
  const [busy, setBusy] = useState(false);

  function resetDemo(scenario?: DemoScenarioId) {
    services.reset(scenario ? { scenario } : undefined);
    setResult(null);
    setBusy(false);
    setRevision((value) => value + 1);
  }

  return (
    <DemoContext.Provider
      value={{ services, revision, result, setResult, busy, setBusy, resetDemo }}
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
