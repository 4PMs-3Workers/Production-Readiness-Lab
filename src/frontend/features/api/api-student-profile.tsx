'use client';

import { useCallback } from 'react';
import { useApi } from './api-provider';
import { useApiResource } from './use-api-resource';
import { ApiFeedback } from './api-feedback';
import { StudentProfileCard } from '@/features/profile/student-profile-card';

export function ApiStudentProfile() {
  const { services } = useApi();
  const load = useCallback((signal: AbortSignal) => services.profile({ signal }), [services]);
  const { state, retry } = useApiResource('student-profile', load);
  return state.status === 'success' ? (
    <StudentProfileCard student={state.data} />
  ) : (
    <ApiFeedback
      loading={state.status === 'loading'}
      message={state.status === 'error' ? state.message : undefined}
      retry={state.status === 'error' ? retry : undefined}
    />
  );
}
