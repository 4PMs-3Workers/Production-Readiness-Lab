import type {
  AvailableSlot,
  BookingDraft,
  BookingResult,
  Purpose,
  Staff,
  StudentContext,
} from '@/types/booking';
import type { DemoConfiguration, MockServiceOptions } from '@/types/demo';

export interface ServiceRequestOptions {
  readonly signal?: AbortSignal;
}

export interface BookingRequestOptions extends ServiceRequestOptions {
  /** Called immediately before handing the booking POST to fetch, after auth checks. */
  readonly onDispatch?: () => void;
}

export interface LoginCredentials {
  readonly email: string;
  readonly password: string;
}

export interface AvailableSlotsQuery {
  readonly purposeId: string;
  readonly staffId: string;
  readonly date: string;
}

export interface BookingServices {
  loginDemo(
    credentials: LoginCredentials,
    options?: ServiceRequestOptions,
  ): Promise<StudentContext>;
  listPurposes(options?: ServiceRequestOptions): Promise<Purpose[]>;
  listStaff(purposeId: string, options?: ServiceRequestOptions): Promise<Staff[]>;
  listAvailableSlots(
    query: AvailableSlotsQuery,
    options?: ServiceRequestOptions,
  ): Promise<AvailableSlot[]>;
  createBooking(draft: BookingDraft, options?: ServiceRequestOptions): Promise<BookingResult>;
}

export interface MockBookingServices extends BookingServices {
  /** Direct booking preview uses a fake student; this does not establish authentication. */
  getDemoStudent(): StudentContext;
  getConfiguration(): DemoConfiguration;
  /** No options: replay current configuration. With options: replace it using factory defaults. */
  reset(options?: MockServiceOptions): void;
}
