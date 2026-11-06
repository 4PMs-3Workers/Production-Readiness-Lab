'use client';

import { useEffect, useRef, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiError, errorMessage } from '@/services/api/errors';
import type { AdminAccount, AdminWriteOptions } from '@/services/admin-contracts';

export interface AccountOperation {
  kind: 'create' | 'update' | 'password';
  targetId?: string;
  label: string;
  revokesSelf?: boolean;
}
type MutationState =
  | { phase: 'idle' }
  | { phase: 'preparing' | 'pending'; operation: AccountOperation }
  | {
      phase: 'confirmed' | 'rejected' | 'conflict' | 'unknown';
      operation: AccountOperation;
      message: string;
      errorCode?: string;
    };

export function useAdminAccountMutation() {
  const { session, revision, refreshSession } = useApi();
  const [state, setState] = useState<MutationState>({ phase: 'idle' });
  const active = useRef<{ controller: AbortController; dispatched: boolean } | null>(null);
  const owner =
    session.status === 'authenticated'
      ? `${session.user.id}:${session.user.sessionGeneration}:${revision}`
      : '';
  const current = useRef({
    owner,
    verifying: session.status === 'authenticated' && session.verifying,
  });
  useEffect(() => {
    current.current = { owner, verifying: session.status === 'authenticated' && session.verifying };
  }, [owner, session]);
  useEffect(
    () => () => {
      active.current?.controller.abort();
    },
    [],
  );
  useEffect(() => {
    const pause = () => {
      if (active.current && !active.current.dispatched) active.current.controller.abort();
    };
    window.addEventListener('focus', pause);
    return () => window.removeEventListener('focus', pause);
  }, []);

  async function run(
    operation: AccountOperation,
    write: (options: AdminWriteOptions) => Promise<AdminAccount>,
  ) {
    if (
      active.current ||
      state.phase === 'unknown' ||
      state.phase === 'conflict' ||
      session.status !== 'authenticated' ||
      session.user.role !== 'ADMIN' ||
      session.verifying
    )
      return null;
    const capturedOwner = owner;
    const request = { controller: new AbortController(), dispatched: false };
    active.current = request;
    setState({ phase: 'preparing', operation });
    try {
      const account = await write({
        expectedUserId: session.user.id,
        expectedSessionGeneration: session.user.sessionGeneration,
        signal: request.controller.signal,
        onDispatch: () => {
          request.controller.signal.throwIfAborted();
          if (current.current.owner !== capturedOwner || current.current.verifying)
            throw new DOMException('Session verification', 'AbortError');
          request.dispatched = true;
          setState({ phase: 'pending', operation });
        },
      });
      if (request.controller.signal.aborted || current.current.owner !== capturedOwner) return null;
      setState({
        phase: 'confirmed',
        operation,
        message:
          operation.kind === 'create'
            ? 'Đã tạo tài khoản.'
            : operation.kind === 'password'
              ? 'Đã đặt lại mật khẩu. Các phiên đăng nhập cũ đã kết thúc.'
              : 'Đã lưu tài khoản.',
      });
      if (operation.revokesSelf) await refreshSession();
      return account;
    } catch (error) {
      if (current.current.owner !== capturedOwner) return null;
      if (!request.dispatched && request.controller.signal.aborted) {
        setState({ phase: 'idle' });
        return null;
      }
      const uncertain =
        request.dispatched && (!(error instanceof ApiError) || error.unknownOutcome);
      const phase = uncertain
        ? 'unknown'
        : error instanceof ApiError && error.code === 'ACCOUNT_VERSION_CONFLICT'
          ? 'conflict'
          : 'rejected';
      setState({
        phase,
        operation,
        errorCode: error instanceof ApiError ? error.code : undefined,
        message: uncertain
          ? 'Chưa xác định kết quả. Yêu cầu có thể đã được lưu. Hãy kiểm tra dữ liệu trước khi bắt đầu thao tác mới.'
          : error instanceof ApiError && error.code === 'VALIDATION_ERROR'
            ? `Thông tin tài khoản chưa hợp lệ. Hãy kiểm tra lại biểu mẫu.${error.requestId ? ` Mã hỗ trợ: ${error.requestId}.` : ''}`
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
    clear: () => {
      if (!active.current) setState({ phase: 'idle' });
    },
    busy: state.phase === 'preparing' || state.phase === 'pending',
  };
}
