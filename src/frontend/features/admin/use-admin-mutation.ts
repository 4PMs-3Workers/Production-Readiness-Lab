'use client';

import { useEffect, useRef, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import type { AdminWriteOptions } from '@/services/admin-contracts';
import type {
  AdminPurpose,
  PurposeCreateBody,
  PurposeReplaceBody,
} from '@/services/admin-catalog-contracts';
import type {
  AdminBookingConfiguration,
  BookingConfigurationReplaceBody,
} from '@/services/admin-configuration-contracts';
import { ApiError, errorMessage } from '@/services/api/errors';

export type AdminOperation =
  | { kind: 'purpose-create'; code: string; body: PurposeCreateBody }
  | { kind: 'purpose-replace'; id: string; code: string; body: PurposeReplaceBody }
  | { kind: 'configuration'; body: BookingConfigurationReplaceBody };
export type AdminReceipt = AdminPurpose | AdminBookingConfiguration;
export type AdminMutationState =
  | { phase: 'idle' }
  | {
      phase: 'preparing' | 'pending' | 'confirmed' | 'rejected' | 'conflict' | 'unknown';
      operation: AdminOperation;
      message: string;
    };

/** One owner-bound lane survives navigation inside the ADMIN workspace. */
export function useAdminMutation() {
  const { services, session, revision, refreshSession } = useApi();
  const [state, setState] = useState<AdminMutationState>({ phase: 'idle' });
  const active = useRef<{ controller: AbortController; dispatched: boolean } | null>(null);
  const unresolved = useRef<AdminOperation | null>(null);
  const owner =
    session.status === 'authenticated'
      ? `${session.user.id}:${session.user.sessionGeneration}:${revision}`
      : '';
  const current = useRef({ owner, verifying: false });
  useEffect(() => {
    current.current = {
      owner,
      verifying: session.status === 'authenticated' && Boolean(session.verifying),
    };
  }, [owner, session]);
  useEffect(() => {
    const pause = () => {
      if (active.current && !active.current.dispatched) active.current.controller.abort();
    };
    window.addEventListener('focus', pause);
    return () => {
      window.removeEventListener('focus', pause);
      active.current?.controller.abort();
    };
  }, []);

  async function run(operation: AdminOperation, replay = false): Promise<AdminReceipt | null> {
    if (
      active.current ||
      session.status !== 'authenticated' ||
      session.user.role !== 'ADMIN' ||
      session.verifying ||
      (!replay && unresolved.current !== null) ||
      (replay && (state.phase !== 'unknown' || operation !== state.operation))
    )
      return null;
    const capturedOwner = owner;
    const request = { controller: new AbortController(), dispatched: false };
    active.current = request;
    setState({ phase: 'preparing', operation, message: 'Đang xác minh yêu cầu…' });
    const options: AdminWriteOptions = {
      expectedUserId: session.user.id,
      expectedSessionGeneration: session.user.sessionGeneration,
      signal: request.controller.signal,
      onDispatch: () => {
        request.controller.signal.throwIfAborted();
        if (current.current.owner !== capturedOwner || current.current.verifying)
          throw new DOMException('Session verification', 'AbortError');
        request.dispatched = true;
        setState({ phase: 'pending', operation, message: 'Đang lưu, chờ backend xác nhận…' });
      },
    };
    try {
      const receipt =
        operation.kind === 'configuration'
          ? await services.replaceAdminBookingConfiguration(operation.body, options)
          : operation.kind === 'purpose-create'
            ? await services.createAdminPurpose(operation.body, options)
            : await services.replaceAdminPurpose(operation.id, operation.body, options);
      if (
        operation.kind !== 'configuration' &&
        (!('code' in receipt) || receipt.code !== operation.code)
      )
        throw new ApiError('SERVICE_UNAVAILABLE');
      if (request.controller.signal.aborted || current.current.owner !== capturedOwner) return null;
      setState({
        phase: 'confirmed',
        operation,
        message: 'Backend đã xác nhận trạng thái yêu cầu.',
      });
      unresolved.current = null;
      return receipt;
    } catch (error) {
      if (current.current.owner !== capturedOwner) return null;
      if (!replay && !request.dispatched && request.controller.signal.aborted) {
        setState({ phase: 'idle' });
        return null;
      }
      const conflict =
        error instanceof ApiError &&
        ['PURPOSE_VERSION_CONFLICT', 'BOOKING_CONFIGURATION_VERSION_CONFLICT'].includes(error.code);
      const unknown =
        replay || (request.dispatched && (!(error instanceof ApiError) || error.unknownOutcome));
      if (unknown || conflict) unresolved.current = operation;
      setState({
        phase: unknown ? 'unknown' : conflict ? 'conflict' : 'rejected',
        operation,
        message: unknown
          ? 'Chưa xác định kết quả. Yêu cầu có thể đã được lưu. Hãy kiểm tra trạng thái hoặc gửi lại đúng yêu cầu cũ.'
          : error instanceof ApiError && error.code === 'VALIDATION_ERROR'
            ? `Giá trị hoặc các khoảng giờ chưa hợp lệ. Hãy kiểm tra lại biểu mẫu.${error.requestId ? ` Mã hỗ trợ: ${error.requestId}.` : ''}`
            : errorMessage(error),
      });
      if (error instanceof ApiError && error.code === 'FORBIDDEN_ROLE') await refreshSession();
      return null;
    } finally {
      if (active.current === request) active.current = null;
    }
  }
  return {
    state,
    run,
    busy: state.phase === 'preparing' || state.phase === 'pending',
    reset: () => {
      if (!active.current) {
        unresolved.current = null;
        setState({ phase: 'idle' });
      }
    },
  };
}
