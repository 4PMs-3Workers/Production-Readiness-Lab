import type { Purpose, Staff, StudentContext } from './booking';

export interface SessionUser extends StudentContext {
  readonly role: 'STUDENT' | 'STAFF' | 'ADMIN';
}

export interface StudentProfile extends StudentContext {
  readonly studentCode: string | null;
  readonly faculty: string | null;
  readonly major: string | null;
  readonly gender: 'MALE' | 'FEMALE' | 'OTHER' | 'UNDISCLOSED' | null;
}

export interface Page<T> {
  readonly items: readonly T[];
  readonly page: number;
  readonly pageSize: number;
  readonly total: number;
}

export interface BookingContext {
  readonly timezone: string;
  readonly businessDate: string;
  readonly minBookingDate: string;
  readonly maxBookingDate: string;
}

export type AppointmentStatus =
  'UPCOMING' | 'CHANGE_PENDING' | 'CANCELLED' | 'COMPLETED' | 'NO_SHOW';

export interface Appointment {
  readonly id: string;
  readonly purpose: Purpose;
  readonly staff: Staff;
  readonly startsAt: string;
  readonly endsAt: string;
  readonly location: string | null;
  readonly status: AppointmentStatus;
  readonly createdAt: string;
}

export interface AppointmentDetail extends Appointment {
  readonly description: string | null;
  readonly cancelledAt: string | null;
  readonly cancelReason: string | null;
}

export interface BookingBody {
  readonly purpose_id: string;
  readonly staff_id: string;
  readonly starts_at: string;
  readonly description?: string;
}

/** An immutable creation receipt, never the current appointment state. */
export interface BookingReceipt {
  readonly id: string;
  readonly status: 'UPCOMING';
  readonly created_at: string;
}
