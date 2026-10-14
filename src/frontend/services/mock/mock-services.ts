import { BUSINESS_TIME_ZONE, getBookingDates, isBusinessDate } from '@/lib/business-time';
import type {
  AvailableSlotsQuery,
  MockBookingServices,
  ServiceRequestOptions,
} from '@/services/contracts';
import { DemoServiceError } from '@/services/errors';
import { createFixtures, DEFAULT_DEMO_NOW } from '@/services/mock/fixtures';
import { getDemoScenario } from '@/services/mock/scenarios';
import type { BookingDraft } from '@/types/booking';
import type { DemoListOutcome, MockServiceOptions } from '@/types/demo';

type Operation = 'login' | 'purposes' | 'staff' | 'slots' | 'booking';

function createState(options: MockServiceOptions) {
  const scenario = getDemoScenario(options.scenario ?? 'success');
  const now = options.now ?? DEFAULT_DEMO_NOW;
  const delayMs = options.delayMs ?? scenario.delayMs;
  if (!Number.isInteger(delayMs) || delayMs < 0 || delayMs > 30_000) {
    throw new RangeError('Demo delay must be an integer between 0 and 30000 milliseconds.');
  }
  const configuration = {
    scenario: scenario.id,
    now,
    timeZone: BUSINESS_TIME_ZONE,
    bookingDates: getBookingDates(now),
    delayMs,
  };
  return {
    configuration,
    scenario,
    fixtures: createFixtures(now),
    controller: new AbortController(),
    requests: new Map<Operation, number>(),
    failedLists: new Set<Operation>(),
    unavailableSlots: new Set<string>(),
    conflictReported: false,
    bookingSequence: 0,
  };
}

type MockState = ReturnType<typeof createState>;

function assertActive(signals: readonly (AbortSignal | undefined)[]): void {
  if (signals.some((signal) => signal?.aborted)) {
    throw new DOMException('Yêu cầu demo đã được hủy.', 'AbortError');
  }
}

function waitForResponse(delayMs: number, signals: readonly (AbortSignal | undefined)[]) {
  assertActive(signals);
  if (delayMs === 0) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      signals.forEach((signal) => signal?.removeEventListener('abort', abort));
    };
    const abort = () => {
      cleanup();
      reject(new DOMException('Yêu cầu demo đã được hủy.', 'AbortError'));
    };
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, delayMs);
    signals.forEach((signal) => signal?.addEventListener('abort', abort, { once: true }));
  });
}

async function request<T>(
  state: MockState,
  operation: Operation,
  options: ServiceRequestOptions | undefined,
  respond: () => T,
): Promise<T> {
  const signals = [state.controller.signal, options?.signal];
  assertActive(signals);
  const ordinal = (state.requests.get(operation) ?? 0) + 1;
  state.requests.set(operation, ordinal);
  const extraDelay =
    (operation === 'staff' || operation === 'slots') && ordinal === 1
      ? state.scenario.firstSelectionExtraDelayMs
      : 0;
  await waitForResponse(state.configuration.delayMs + extraDelay, signals);
  // Reset/cancellation between timer resolution and this continuation also invalidates the request.
  assertActive(signals);
  return respond();
}

function serviceUnavailable(): never {
  throw new DemoServiceError(
    'SERVICE_UNAVAILABLE',
    'Dịch vụ demo tạm thời không khả dụng. Vui lòng thử lại.',
  );
}

function invalidRequest(): never {
  throw new DemoServiceError(
    'INVALID_REQUEST',
    'Lựa chọn demo không hợp lệ. Vui lòng kiểm tra lại.',
  );
}

function bookingConflict(): never {
  throw new DemoServiceError(
    'BOOKING_CONFLICT',
    'Giờ này không còn trống. Vui lòng chọn giờ khác.',
  );
}

function listIsEmpty(state: MockState, operation: Operation, outcome: DemoListOutcome): boolean {
  if (outcome === 'error-once' && !state.failedLists.has(operation)) {
    state.failedLists.add(operation);
    serviceUnavailable();
  }
  return outcome === 'empty';
}

function getPurpose(state: MockState, id: string) {
  return state.fixtures.purposes.find((purpose) => purpose.id === id) ?? invalidRequest();
}

function getStaff(state: MockState, purposeId: string, staffId: string) {
  const associated = state.fixtures.associations.some(
    (pair) => pair.purposeId === purposeId && pair.staffId === staffId,
  );
  return (
    (associated && state.fixtures.staff.find((staff) => staff.id === staffId)) || invalidRequest()
  );
}

/** Create one instance per demo session/provider. No singleton state or backend calls. */
export function createMockServices(options: MockServiceOptions = {}): MockBookingServices {
  let currentOptions = { ...options };
  let state = createState(currentOptions);

  return {
    getDemoStudent() {
      return { ...state.fixtures.student };
    },
    getConfiguration() {
      return { ...state.configuration, bookingDates: [...state.configuration.bookingDates] };
    },
    reset(nextOptions = currentOptions) {
      const nextState = createState(nextOptions);
      state.controller.abort();
      currentOptions = { ...nextOptions };
      state = nextState;
    },
    loginDemo(credentials, requestOptions) {
      const current = state;
      // Credentials are transient call input; outcomes use the preset, not a stored password.
      const inputValid =
        /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(credentials.email.trim()) &&
        credentials.password.length > 0;
      return request(current, 'login', requestOptions, () => {
        if (current.scenario.login === 'error') serviceUnavailable();
        if (!inputValid || current.scenario.login === 'invalid') {
          throw new DemoServiceError(
            'INVALID_CREDENTIALS',
            'Thông tin đăng nhập demo không hợp lệ.',
          );
        }
        return { ...current.fixtures.student };
      });
    },
    listPurposes(requestOptions) {
      const current = state;
      return request(current, 'purposes', requestOptions, () => {
        if (listIsEmpty(current, 'purposes', current.scenario.purposes)) return [];
        return current.fixtures.purposes.map((purpose) => ({ ...purpose }));
      });
    },
    listStaff(purposeId, requestOptions) {
      const current = state;
      return request(current, 'staff', requestOptions, () => {
        getPurpose(current, purposeId);
        if (listIsEmpty(current, 'staff', current.scenario.staff)) return [];
        return current.fixtures.staff
          .filter((staff) =>
            current.fixtures.associations.some(
              (pair) => pair.purposeId === purposeId && pair.staffId === staff.id,
            ),
          )
          .map((staff) => ({ ...staff }));
      });
    },
    listAvailableSlots(query: AvailableSlotsQuery, requestOptions) {
      const current = state;
      const selection = { ...query };
      return request(current, 'slots', requestOptions, () => {
        getPurpose(current, selection.purposeId);
        getStaff(current, selection.purposeId, selection.staffId);
        if (!isBusinessDate(selection.date)) invalidRequest();
        if (listIsEmpty(current, 'slots', current.scenario.slots)) return [];
        return current.fixtures.slots
          .filter(
            (slot) =>
              slot.purposeId === selection.purposeId &&
              slot.staffId === selection.staffId &&
              slot.date === selection.date &&
              !current.unavailableSlots.has(slot.id),
          )
          .map((slot) => ({ ...slot }));
      });
    },
    createBooking(draft: BookingDraft, requestOptions) {
      const current = state;
      const selection = { ...draft };
      return request(current, 'booking', requestOptions, () => {
        const purpose = getPurpose(current, selection.purposeId);
        const staff = getStaff(current, purpose.id, selection.staffId);
        const slot = current.fixtures.slots.find(
          (slot) =>
            slot.id === selection.slotId &&
            slot.purposeId === purpose.id &&
            slot.staffId === staff.id &&
            slot.date === selection.date,
        );
        const description = selection.description.trim();
        if (
          !slot ||
          selection.studentId !== current.fixtures.student.id ||
          (purpose.requiresFreeText && !description)
        )
          invalidRequest();
        if (current.unavailableSlots.has(slot.id)) bookingConflict();
        if (current.scenario.booking === 'error') serviceUnavailable();
        if (current.scenario.booking === 'conflict-once' && !current.conflictReported) {
          current.conflictReported = true;
          current.unavailableSlots.add(slot.id);
          bookingConflict();
        }
        current.unavailableSlots.add(slot.id);
        current.bookingSequence += 1;
        const sequence = String(current.bookingSequence).padStart(4, '0');
        return {
          id: `booking-demo-${sequence}`,
          reference: `DEMO-${sequence}`,
          student: { ...current.fixtures.student },
          purpose: { ...purpose },
          staff: { ...staff },
          slot: { ...slot },
          description: description || null,
          createdAt: current.configuration.now,
          isDemo: true as const,
        };
      });
    },
  };
}
