// Shared wizard choices and mock-only booking results. Real API DTOs live in api.ts.
export type PurposeCode = string;

export interface StudentContext {
  readonly id: string;
  readonly displayName: string;
  readonly email: string;
}

export interface Purpose {
  readonly id: string;
  readonly code: PurposeCode;
  readonly name: string;
  readonly durationMinutes: number;
  readonly requiresFreeText: boolean;
}

export interface Staff {
  readonly id: string;
  readonly displayName: string;
}

export interface AvailableSlot {
  readonly id: string;
  readonly purposeId: string;
  readonly staffId: string;
  /** Business date, YYYY-MM-DD, in Asia/Ho_Chi_Minh. */
  readonly date: string;
  /** ISO 8601 instants with Z or an explicit offset. */
  readonly startsAt: string;
  readonly endsAt: string;
  readonly location: string | null;
}

/** Complete selection submitted to the service; partial wizard state belongs in the UI. */
export interface BookingDraft {
  readonly studentId: string;
  readonly purposeId: string;
  readonly staffId: string;
  readonly date: string;
  readonly slotId: string;
  readonly description: string;
}

export interface BookingResult {
  readonly id: string;
  readonly reference: string;
  readonly student: StudentContext;
  readonly purpose: Purpose;
  readonly staff: Staff;
  readonly slot: AvailableSlot;
  readonly description: string | null;
  readonly createdAt: string;
  readonly isDemo: true;
}
