'use client';

import { useCallback } from 'react';
import { ApiError } from '@/services/api/errors';
import { parseBookingContext } from '@/services/api/booking-context';
import { useApi } from './api-provider';
import { useApiResource } from './use-api-resource';

export const UTC_TIMEZONE_FALLBACK_MESSAGE =
  'Đang hiển thị giờ UTC vì chưa lấy được múi giờ dịch vụ.';

export type AppointmentTimezoneState =
  | { status: 'loading'; timezone: 'UTC' }
  | { status: 'ready'; timezone: string }
  | { status: 'fallback'; timezone: 'UTC' }
  | { status: 'error'; timezone: 'UTC'; message: string };

export function useAppointmentTimezone() {
  const { services } = useApi();
  const load = useCallback(
    async (signal: AbortSignal) => parseBookingContext(await services.context({ signal })),
    [services],
  );
  const { state, retry } = useApiResource('appointment-timezone', load);
  let timezone: AppointmentTimezoneState;
  if (state.status === 'loading') timezone = { status: 'loading', timezone: 'UTC' };
  else if (state.status === 'success')
    timezone = { status: 'ready', timezone: state.data.timezone };
  else if (
    state.error instanceof ApiError &&
    (state.error.status === 401 || state.error.status === 403)
  )
    timezone = { status: 'error', timezone: 'UTC', message: state.message };
  else timezone = { status: 'fallback', timezone: 'UTC' };
  return { state: timezone, retry };
}
