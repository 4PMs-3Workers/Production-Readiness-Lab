export interface PurposeValues {
  readonly name: string;
  readonly duration_minutes: number;
  readonly requires_free_text: boolean;
  readonly is_active: boolean;
}
export interface AdminPurpose extends PurposeValues {
  readonly id: string;
  readonly code: string;
  /** Opaque timestamp: preserve the original microseconds and offset. */
  readonly updated_at: string;
}
export interface PurposeCreateBody extends PurposeValues {
  readonly code: string;
}
export interface PurposeReplaceBody extends PurposeValues {
  readonly expected_updated_at: string;
}
export interface AdminPurposeQuery {
  readonly page: number;
  readonly pageSize?: number;
  readonly isActive?: boolean;
}
