export type DemoScenarioId =
  | 'success'
  | 'slow'
  | 'invalid-login'
  | 'login-error'
  | 'empty-purposes'
  | 'empty-staff'
  | 'empty-slots'
  | 'purposes-error'
  | 'staff-error'
  | 'slots-error'
  | 'booking-error'
  | 'conflict-once'
  | 'stale-response';

export type DemoListOutcome = 'success' | 'empty' | 'error-once';

export interface DemoScenario {
  readonly id: DemoScenarioId;
  readonly label: string;
  readonly description: string;
  readonly delayMs: number;
  readonly login: 'success' | 'invalid' | 'error';
  readonly purposes: DemoListOutcome;
  readonly staff: DemoListOutcome;
  readonly slots: DemoListOutcome;
  readonly booking: 'success' | 'error' | 'conflict-once';
  readonly firstSelectionExtraDelayMs: number;
}

export interface MockServiceOptions {
  readonly scenario?: DemoScenarioId;
  /** ISO 8601 instant with Z or offset; never derived from the machine clock. */
  readonly now?: string;
  /** Optional override for the preset's normal response delay. */
  readonly delayMs?: number;
}

export interface DemoConfiguration {
  readonly scenario: DemoScenarioId;
  readonly now: string;
  readonly timeZone: 'Asia/Ho_Chi_Minh';
  readonly bookingDates: readonly string[];
  readonly delayMs: number;
}
