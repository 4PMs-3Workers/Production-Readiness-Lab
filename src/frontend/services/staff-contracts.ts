import type { Page } from '@/types/api';

export type StaffAppointmentStatus =
  'UPCOMING' | 'CHANGE_PENDING' | 'CANCELLED' | 'COMPLETED' | 'NO_SHOW';

export type StaffOutcomeStatus = 'COMPLETED' | 'NO_SHOW';

export interface StaffContext {
  readonly timezone: string;
  readonly businessDate: string;
}

export interface StaffAppointment {
  readonly id: string;
  readonly purposeId: string;
  readonly purposeCode: string;
  readonly purposeName: string;
  readonly studentId: string;
  readonly studentName: string;
  readonly durationMinutes: number;
  readonly startsAt: string;
  readonly endsAt: string;
  readonly location: string | null;
  readonly status: StaffAppointmentStatus;
  readonly createdAt: string;
  readonly cancelledAt: string | null;
  readonly outcomeAt: string | null;
}

export interface StaffAppointmentDetail extends StaffAppointment {
  readonly description: string | null;
  readonly cancelledBy: string | null;
  readonly cancelReason: string | null;
  readonly outcomeBy: string | null;
}

export interface StaffAppointmentQuery {
  readonly page?: number;
  readonly pageSize?: number;
  readonly status?: StaffAppointmentStatus;
  readonly date?: string;
}

export interface StaffPurpose {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly durationMinutes: number;
  readonly requiresFreeText: boolean;
  readonly isActive: boolean;
  readonly isAccepting: boolean;
}

export interface StaffUnavailability {
  readonly id: string;
  readonly startsAt: string;
  readonly endsAt: string;
  readonly createdAt: string;
}

export interface StaffUnavailabilityQuery {
  readonly from?: string;
  readonly to?: string;
  readonly page?: number;
  readonly pageSize?: number;
}

export type StaffPage<T> = Page<T>;
