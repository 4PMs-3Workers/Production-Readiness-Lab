export { createMockServices } from '@/services/mock/mock-services';
export { DEMO_SCENARIOS } from '@/services/mock/scenarios';
export { DemoServiceError, isRequestCancelled } from '@/services/errors';
export type {
  AvailableSlotsQuery,
  BookingServices,
  LoginCredentials,
  MockBookingServices,
  ServiceRequestOptions,
} from '@/services/contracts';
export type { DemoConfiguration, DemoScenarioId, MockServiceOptions } from '@/types/demo';
