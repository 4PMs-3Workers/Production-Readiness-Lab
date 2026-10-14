export type DemoServiceErrorCode =
  'INVALID_CREDENTIALS' | 'SERVICE_UNAVAILABLE' | 'BOOKING_CONFLICT' | 'INVALID_REQUEST';

export class DemoServiceError extends Error {
  constructor(
    readonly code: DemoServiceErrorCode,
    message: string,
  ) {
    super(message);
    this.name = 'DemoServiceError';
  }
}

export function isRequestCancelled(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}
