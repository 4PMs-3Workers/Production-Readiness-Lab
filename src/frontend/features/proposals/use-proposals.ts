'use client';

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiError, errorMessage } from '@/services/api/errors';
import { ProposalRejection, type ApiServices } from '@/services/api/client';
import type {
  ProposalAccessOptions,
  ProposalHistoryItem,
  ProposalHistoryPage,
} from '@/services/proposal-contracts';
import type { StoredProposalIntent } from '@/services/api/proposal-intent';
import type { ProposalAppointment } from './proposal-form';

export interface ProposalRead {
  readonly history: ProposalHistoryPage;
  readonly pending: ProposalHistoryItem | null;
}

function contextKey(page: ProposalHistoryPage) {
  const value = page.context;
  return `${value.appointmentId}:${value.appointmentStatus}:${value.proposalVersion}:${value.pendingProposalId}`;
}

/** Match the server's current ID, including when the selected history page is empty. */
export async function readProposalPage(
  services: ApiServices,
  appointmentId: string,
  page: number,
  options: ProposalAccessOptions,
): Promise<ProposalRead> {
  const history = await services.proposalHistory(appointmentId, page, options);
  const pendingId = history.context.pendingProposalId;
  if (!pendingId) return { history, pending: null };
  let pending = history.items.find((item) => item.id === pendingId);
  if (!pending) {
    const first = page === 1 ? history : await services.proposalHistory(appointmentId, 1, options);
    if (contextKey(first) !== contextKey(history))
      throw new ApiError('PROPOSAL_PRECONDITION_FAILED', 409);
    pending = first.items.find((item) => item.id === pendingId);
    // A bounded lookup fails closed if a very large or changing history cannot supply the ID.
    const pages = Math.min(50, Math.ceil(first.total / first.pageSize));
    for (let index = 2; !pending && index <= pages; index += 1) {
      const next =
        index === page ? history : await services.proposalHistory(appointmentId, index, options);
      if (contextKey(next) !== contextKey(history))
        throw new ApiError('PROPOSAL_PRECONDITION_FAILED', 409);
      pending = next.items.find((item) => item.id === pendingId);
    }
  }
  if (!pending || pending.status !== 'PENDING') throw new ApiError('SERVICE_UNAVAILABLE');
  return { history, pending };
}

type ReadState =
  | { status: 'loading' }
  | { status: 'success'; page: number; data: ProposalRead }
  | { status: 'error'; error: unknown };

export function useProposals({
  appointment,
  userId,
  role,
  onRefresh,
}: {
  appointment: ProposalAppointment;
  userId: string;
  role: 'STUDENT' | 'STAFF';
  onRefresh: (signal: AbortSignal) => Promise<unknown>;
}) {
  const { services, proposalIntents } = useApi();
  const [page, setPage] = useState(1);
  const [read, setRead] = useState<ReadState>({ status: 'loading' });
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const onRefreshRef = useRef(onRefresh);
  const appointmentStatus = useRef(appointment.status);
  useEffect(() => {
    onRefreshRef.current = onRefresh;
    appointmentStatus.current = appointment.status;
  }, [onRefresh, appointment.status]);
  const readController = useRef<AbortController | null>(null);
  const reading = useRef(false);
  const write = useRef<{
    controller: AbortController;
    saved: StoredProposalIntent;
    version: number;
  } | null>(null);
  const lastContext = useRef<string | null>(null);
  const saved = useSyncExternalStore(
    proposalIntents.subscribe,
    () => proposalIntents.get(userId, role, appointment.id),
    () => null,
  );
  const storageIssue = useSyncExternalStore(
    proposalIntents.subscribe,
    () => proposalIntents.getIssue(userId, role, appointment.id),
    () => null,
  );

  const refresh = useCallback(
    async (forceDetail = false, external?: AbortSignal) => {
      readController.current?.abort();
      const controller = new AbortController();
      readController.current = controller;
      reading.current = true;
      const signal = external ? AbortSignal.any([external, controller.signal]) : controller.signal;
      setRefreshing(true);
      try {
        const data = await readProposalPage(services, appointment.id, page, {
          expectedUserId: userId,
          expectedRole: role,
          signal,
        });
        const nextContext = contextKey(data.history);
        const pendingIntent = proposalIntents.get(userId, role, appointment.id);
        if (
          forceDetail ||
          pendingIntent?.phase === 'confirmed' ||
          (lastContext.current !== null && lastContext.current !== nextContext) ||
          data.history.context.appointmentStatus !== appointmentStatus.current
        )
          await onRefreshRef.current(signal);
        signal.throwIfAborted();
        lastContext.current = nextContext;
        setRead({ status: 'success', page, data });
        if (pendingIntent?.phase === 'confirmed') {
          proposalIntents.clear(pendingIntent.operation, pendingIntent.version);
          setMessage('Đã cập nhật trạng thái lịch và đề xuất từ hệ thống.');
        }
        return data;
      } catch (cause) {
        if (!signal.aborted) setRead({ status: 'error', error: cause });
        throw cause;
      } finally {
        if (readController.current === controller) {
          reading.current = false;
          if (!signal.aborted) setRefreshing(false);
        }
      }
    },
    [services, proposalIntents, appointment.id, userId, role, page],
  );

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh().catch(() => {}), 0);
    const update = () => {
      if (!write.current && !reading.current && document.visibilityState !== 'hidden')
        void refresh().catch(() => {});
    };
    const interval = window.setInterval(update, 10_000);
    window.addEventListener('focus', update);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(initial);
      window.removeEventListener('focus', update);
      readController.current?.abort();
    };
  }, [refresh]);

  useEffect(
    () => () => {
      const active = write.current;
      if (active) {
        try {
          proposalIntents.markUnknown(active.saved.operation, active.version);
        } catch {
          /* The pre-dispatch persisted record remains available for reload recovery. */
        }
        active.controller.abort();
      }
    },
    [proposalIntents],
  );

  async function run(intent: StoredProposalIntent): Promise<boolean> {
    if (
      write.current ||
      intent.operation.userId !== userId ||
      intent.operation.appointmentId !== appointment.id
    )
      return false;
    let version: number | null;
    try {
      version = proposalIntents.begin(intent.operation);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không thể lưu thao tác để phục hồi.');
      return false;
    }
    if (version === null) return false;
    const controller = new AbortController();
    const active = { controller, saved: intent, version };
    write.current = active;
    setBusy(true);
    setError('');
    setMessage('');
    let confirmed = false;
    let dispatched = false;
    try {
      const options = {
        expectedUserId: userId,
        signal: controller.signal,
        onDispatch: () => {
          dispatched = true;
        },
      };
      const operation = intent.operation;
      if (operation.kind === 'create')
        await services.createProposal(appointment.id, operation.body, operation.key, options);
      else
        await services.decideProposal(
          appointment.id,
          operation.proposalId,
          operation.action,
          options,
        );
      controller.signal.throwIfAborted();
      if (!proposalIntents.isCurrent(operation, version)) return false;
      proposalIntents.confirm(operation, version);
      confirmed = true;
      await refresh(true, controller.signal);
      return true;
    } catch (cause) {
      if (controller.signal.aborted || !proposalIntents.isCurrent(intent.operation, version))
        return false;
      try {
        if (confirmed) {
          setMessage('Thao tác đã được ghi nhận. Hãy tải lại trạng thái hiện tại.');
        } else if (cause instanceof ApiError && cause.code === 'IDEMPOTENCY_CONFLICT') {
          proposalIntents.markConflict(intent.operation, version);
        } else if (
          cause instanceof ProposalRejection ||
          (intent.phase === 'ready' && cause instanceof ApiError && !cause.unknownOutcome)
        ) {
          proposalIntents.clear(intent.operation, version);
          await refresh(true, controller.signal).catch(() => {});
        } else {
          // Even a later auth/validation failure cannot resolve an earlier unknown commit.
          proposalIntents.markUnknown(intent.operation, version);
        }
        setError(
          cause instanceof ApiError
            ? errorMessage(cause)
            : cause instanceof Error
              ? cause.message
              : 'Chưa xác định được kết quả thao tác.',
        );
      } catch (storageError) {
        setError(
          storageError instanceof Error
            ? storageError.message
            : 'Không thể cập nhật dữ liệu phục hồi.',
        );
      }
      // A newly rejected, undispatched request or a definitive business failure permits a revised intention.
      return (
        confirmed ||
        cause instanceof ProposalRejection ||
        (intent.phase === 'ready' &&
          !dispatched &&
          cause instanceof ApiError &&
          !cause.unknownOutcome)
      );
    } finally {
      if (write.current === active) {
        write.current = null;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
  }

  return {
    page,
    setPage,
    state: read.status === 'success' && read.page !== page ? { status: 'loading' as const } : read,
    refreshing,
    refresh: () =>
      refresh(true)
        .then(() => setError(''))
        .catch(() => {}),
    busy,
    saved,
    storageIssue,
    error,
    setError,
    message,
    run,
  };
}
