'use client';

import Link from 'next/link';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
import { useApiResource } from '@/features/api/use-api-resource';
import { ApiError, errorMessage } from '@/services/api/errors';
import type { StaffChangeIntent } from '@/services/api/staff-change-intent';
import type {
  StaffAppointment,
  StaffAppointmentDetail,
  StaffAppointmentStatus,
  StaffOutcomeStatus,
} from '@/services/staff-contracts';
import {
  formatBusinessDate,
  formatBusinessTime,
  formatTimeZoneLabel,
  getBusinessDate,
} from '@/lib/business-time';
import styles from '@/features/booking/booking.module.css';
import staffStyles from './staff-appointments.module.css';

const STATUS_LABELS: Record<StaffAppointmentStatus, string> = {
  UPCOMING: 'Sắp diễn ra',
  CHANGE_PENDING: 'Chờ xác nhận đổi lịch',
  CANCELLED: 'Đã hủy',
  COMPLETED: 'Đã hoàn thành',
  NO_SHOW: 'Vắng mặt',
};

function useStaffContext() {
  const { services } = useApi();
  const load = useCallback((signal: AbortSignal) => services.staffContext({ signal }), [services]);
  return useApiResource('staff-business-context', load);
}

function AppointmentTime({
  appointment,
  timezone,
}: {
  appointment: StaffAppointment;
  timezone: string;
}) {
  return (
    <>
      {formatBusinessDate(getBusinessDate(appointment.startsAt, timezone))} ·{' '}
      {formatBusinessTime(appointment.startsAt, timezone)}–
      {formatBusinessTime(appointment.endsAt, timezone)}
    </>
  );
}

export function StaffAppointmentList() {
  const { services } = useApi();
  const context = useStaffContext();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<StaffAppointmentStatus | ''>('');
  const [dateDraft, setDateDraft] = useState('');
  const [date, setDate] = useState('');
  const load = useCallback(
    (signal: AbortSignal) =>
      services.staffAppointments(
        { page, pageSize: 20, ...(status ? { status } : {}), ...(date ? { date } : {}) },
        { signal },
      ),
    [services, page, status, date],
  );
  const appointments = useApiResource(`staff-appointments:${page}:${status}:${date}`, load);
  const appointmentState = appointments.state;

  if (context.state.status !== 'success')
    return (
      <ApiFeedback
        loading={context.state.status === 'loading'}
        message={context.state.status === 'error' ? context.state.message : undefined}
        retry={context.state.status === 'error' ? context.retry : undefined}
      />
    );
  const timezone = context.state.data.timezone;
  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>LỊCH TƯ VẤN</p>
          <h1>Lịch được giao</h1>
          <p>Lịch và ngày được hiển thị theo {formatTimeZoneLabel(timezone)}.</p>
        </div>
      </div>
      <section className={staffStyles.filters} aria-label="Lọc lịch được giao">
        <label>
          Trạng thái lịch
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as StaffAppointmentStatus | '');
              setPage(1);
            }}
          >
            <option value="">Tất cả trạng thái</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ngày nghiệp vụ
          <input
            type="date"
            value={dateDraft}
            onChange={(event) => setDateDraft(event.target.value)}
          />
        </label>
        <button
          className={styles.secondaryButton}
          type="button"
          onClick={() => {
            setDate(dateDraft);
            setPage(1);
          }}
        >
          Áp dụng bộ lọc
        </button>
        <button className={styles.secondaryButton} type="button" onClick={appointments.retry}>
          Làm mới
        </button>
      </section>
      {appointmentState.status !== 'success' ? (
        <ApiFeedback
          loading={appointmentState.status === 'loading'}
          message={appointmentState.status === 'error' ? appointmentState.message : undefined}
          retry={appointmentState.status === 'error' ? appointments.retry : undefined}
        />
      ) : (
        <>
          <p className={styles.historyCount} role="status">
            {appointmentState.data.total} lịch · {formatTimeZoneLabel(timezone)}
          </p>
          {appointmentState.data.items.length === 0 ? (
            <section className={styles.resultCard}>
              <div className={styles.emptyState}>
                <h2>Chưa có lịch phù hợp.</h2>
                <p>Thử thay đổi bộ lọc hoặc tải lại danh sách.</p>
              </div>
            </section>
          ) : (
            <ol className={styles.historyList} aria-label="Các lịch được giao">
              {appointmentState.data.items.map((appointment) => (
                <li key={appointment.id}>
                  <article className={styles.historyCard}>
                    <header className={styles.historyHeader}>
                      <span className={styles.historyReference}>{appointment.id}</span>
                      <span>{STATUS_LABELS[appointment.status]}</span>
                    </header>
                    <h2>{appointment.purposeName}</h2>
                    <dl className={styles.historyDetails}>
                      <div>
                        <dt>Sinh viên</dt>
                        <dd>{appointment.studentName}</dd>
                      </div>
                      <div>
                        <dt>Thời gian</dt>
                        <dd>
                          <AppointmentTime appointment={appointment} timezone={timezone} />
                          {' · '}
                          {appointment.durationMinutes} phút
                        </dd>
                      </div>
                      <div>
                        <dt>Địa điểm</dt>
                        <dd>{appointment.location ?? 'Địa điểm sẽ được thông báo sau'}</dd>
                      </div>
                    </dl>
                    <Link
                      className={styles.secondaryButton}
                      href={`/staff/appointments/${encodeURIComponent(appointment.id)}`}
                    >
                      Xem chi tiết<span className={styles.srOnly}> lịch {appointment.id}</span> →
                    </Link>
                  </article>
                </li>
              ))}
            </ol>
          )}
          <Pagination page={appointmentState.data} onPage={setPage} />
        </>
      )}
    </div>
  );
}

export function StaffAppointmentDetailView({ appointmentId }: { appointmentId: string }) {
  return <StaffAppointmentDetailContent key={appointmentId} appointmentId={appointmentId} />;
}

function StaffAppointmentDetailContent({ appointmentId }: { appointmentId: string }) {
  const { services } = useApi();
  const context = useStaffContext();
  const [confirmed, setConfirmed] = useState<{ id: string; detail: StaffAppointmentDetail } | null>(
    null,
  );
  const load = useCallback(
    (signal: AbortSignal) => services.staffAppointment(appointmentId, { signal }),
    [services, appointmentId],
  );
  const detailResource = useApiResource(`staff-appointment:${appointmentId}`, load);

  if (context.state.status !== 'success')
    return (
      <ApiFeedback
        loading={context.state.status === 'loading'}
        message={context.state.status === 'error' ? context.state.message : undefined}
        retry={context.state.status === 'error' ? context.retry : undefined}
      />
    );
  if (detailResource.state.status !== 'success')
    return (
      <ApiFeedback
        loading={detailResource.state.status === 'loading'}
        message={detailResource.state.status === 'error' ? detailResource.state.message : undefined}
        retry={detailResource.state.status === 'error' ? detailResource.retry : undefined}
      />
    );

  const appointment =
    confirmed?.id === appointmentId ? confirmed.detail : detailResource.state.data;
  const refresh = async (signal?: AbortSignal) => {
    const latest = await services.staffAppointment(appointmentId, signal ? { signal } : undefined);
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    setConfirmed({ id: appointmentId, detail: latest });
    return latest;
  };
  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>CHI TIẾT LỊCH</p>
          <h1>{appointment.purposeName}</h1>
          <p>Mã lịch: {appointment.id}</p>
        </div>
        <Link className={styles.secondaryButton} href="/staff/appointments">
          Quay lại danh sách
        </Link>
      </div>
      <section className={styles.resultCard}>
        <dl className={styles.historyDetails}>
          <div>
            <dt>Sinh viên</dt>
            <dd>{appointment.studentName}</dd>
          </div>
          <div>
            <dt>Ngày, giờ ({formatTimeZoneLabel(context.state.data.timezone)})</dt>
            <dd>
              <AppointmentTime appointment={appointment} timezone={context.state.data.timezone} />
              {' · '}
              {appointment.durationMinutes} phút
            </dd>
          </div>
          <div>
            <dt>Trạng thái</dt>
            <dd>{STATUS_LABELS[appointment.status]}</dd>
          </div>
          <div>
            <dt>Địa điểm</dt>
            <dd>{appointment.location ?? 'Địa điểm sẽ được thông báo sau'}</dd>
          </div>
          <div className={staffStyles.description}>
            <dt>Nội dung trao đổi</dt>
            <dd>{appointment.description ?? 'Sinh viên chưa cung cấp nội dung.'}</dd>
          </div>
          {appointment.cancelReason && (
            <div className={staffStyles.description}>
              <dt>Lý do hủy</dt>
              <dd>{appointment.cancelReason}</dd>
            </div>
          )}
          {appointment.cancelledAt && (
            <div>
              <dt>Thời điểm hủy</dt>
              <dd>
                {formatBusinessDate(
                  getBusinessDate(appointment.cancelledAt, context.state.data.timezone),
                )}
                {' · '}
                {formatBusinessTime(appointment.cancelledAt, context.state.data.timezone)}
              </dd>
            </div>
          )}
          {appointment.cancelledBy && (
            <div>
              <dt>Người hủy</dt>
              <dd>{appointment.cancelledBy}</dd>
            </div>
          )}
          {appointment.outcomeAt && (
            <div>
              <dt>Thời điểm ghi kết quả</dt>
              <dd>
                {formatBusinessDate(
                  getBusinessDate(appointment.outcomeAt, context.state.data.timezone),
                )}
                {' · '}
                {formatBusinessTime(appointment.outcomeAt, context.state.data.timezone)}
              </dd>
            </div>
          )}
          {appointment.outcomeBy && (
            <div>
              <dt>Người ghi kết quả</dt>
              <dd>{appointment.outcomeBy}</dd>
            </div>
          )}
        </dl>
      </section>
      <StaffAppointmentActions key={appointment.id} appointment={appointment} onReload={refresh} />
    </div>
  );
}

export function StaffAppointmentActions({
  appointment,
  onReload,
}: {
  appointment: StaffAppointmentDetail;
  onReload: (signal: AbortSignal) => Promise<StaffAppointmentDetail>;
}) {
  const { services, session, staffChanges } = useApi();
  const expectedUserId =
    session.status === 'authenticated' && session.user.role === 'STAFF' ? session.user.id : null;
  const [showCancel, setShowCancel] = useState(false);
  const [outcomeToConfirm, setOutcomeToConfirm] = useState<StaffOutcomeStatus | null>(null);
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const savedChange = useSyncExternalStore(
    staffChanges.subscribe,
    () => staffChanges.get(expectedUserId, appointment.id),
    () => null,
  );
  const unresolved = savedChange?.change ?? null;
  const [refreshRequired, setRefreshRequired] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const cancelTrigger = useRef<HTMLButtonElement>(null);
  const cancelReason = useRef<HTMLTextAreaElement>(null);
  const completeTrigger = useRef<HTMLButtonElement>(null);
  const noShowTrigger = useRef<HTMLButtonElement>(null);
  const outcomeConfirm = useRef<HTMLButtonElement>(null);
  const cancelDialog = useRef<HTMLElement>(null);
  const outcomeDialog = useRef<HTMLElement>(null);
  const cancellable = appointment.status === 'UPCOMING' || appointment.status === 'CHANGE_PENDING';
  const actionsLocked = pending || unresolved !== null || refreshRequired;
  useEffect(
    () => () => {
      if (activeRequest.current) {
        staffChanges.markUnknown(expectedUserId, appointment.id);
        activeRequest.current.abort();
      }
    },
    [staffChanges, expectedUserId, appointment.id],
  );
  useEffect(() => {
    if (showCancel) cancelReason.current?.focus();
  }, [showCancel]);
  useEffect(() => {
    if (outcomeToConfirm) outcomeConfirm.current?.focus();
  }, [outcomeToConfirm]);
  useEffect(() => {
    if (pending) (showCancel ? cancelDialog : outcomeDialog).current?.focus();
  }, [pending, showCancel]);

  function closeCancel(force = false) {
    if (pending && !force) return;
    setShowCancel(false);
    cancelTrigger.current?.focus();
  }

  function closeOutcome(force = false) {
    if (pending && !force) return;
    const trigger = outcomeToConfirm === 'COMPLETED' ? completeTrigger : noShowTrigger;
    setOutcomeToConfirm(null);
    trigger.current?.focus();
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLElement>, close: () => void) {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not(:disabled), textarea:not(:disabled)',
    );
    if (!controls.length) {
      event.preventDefault();
      event.currentTarget.focus();
      return;
    }
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function dismissDialog() {
    if (showCancel) closeCancel(true);
    if (outcomeToConfirm) closeOutcome(true);
  }

  function confirmLatest(change: StaffChangeIntent) {
    staffChanges.clear(change.userId, change.appointmentId);
    setRefreshRequired(false);
    setMessage(change.status === 'CANCELLED' ? 'Đã hủy lịch' : 'Đã cập nhật kết quả');
    setError('');
    setReason('');
    dismissDialog();
  }

  function reconcile(
    change: StaffChangeIntent,
    latest: StaffAppointmentDetail | null,
    cause: unknown,
    resolvedRejection: boolean,
  ) {
    if (resolvedRejection) {
      dismissDialog();
      staffChanges.clear(change.userId, change.appointmentId);
      setRefreshRequired(latest === null);
      setError(errorMessage(cause));
      return;
    }
    if (latest?.status === change.status) {
      confirmLatest(change);
      return;
    }
    dismissDialog();
    if (latest && ['CANCELLED', 'COMPLETED', 'NO_SHOW'].includes(latest.status)) {
      staffChanges.clear(change.userId, change.appointmentId);
      setError('Lịch đã có trạng thái khác. Vui lòng kiểm tra chi tiết mới nhất.');
      return;
    }
    staffChanges.markUnknown(change.userId, change.appointmentId);
    setError(errorMessage(cause));
  }

  async function runChange(change: StaffChangeIntent) {
    if (pending || change.appointmentId !== appointment.id || change.userId !== expectedUserId)
      return;
    const wasUnknown = staffChanges.get(change.userId, change.appointmentId)?.phase === 'unknown';
    if (!staffChanges.begin(change)) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setPending(true);
    setError('');
    setMessage('');
    let accepted = false;
    try {
      if (change.status === 'CANCELLED')
        await services.cancelStaffAppointment(change.appointmentId, change.reason, {
          signal: controller.signal,
          expectedUserId: change.userId,
        });
      else
        await services.recordStaffOutcome(change.appointmentId, change.status, {
          signal: controller.signal,
          expectedUserId: change.userId,
        });
      accepted = true;
      const latest = await onReload(controller.signal);
      if (controller.signal.aborted) return;
      reconcile(change, latest, new ApiError('TIMEOUT'), false);
    } catch (cause) {
      if (controller.signal.aborted || (cause instanceof Error && cause.name === 'AbortError'))
        return;
      if (cause instanceof ApiError && cause.code === 'ACCOUNT_CHANGED') {
        staffChanges.clearAll();
        return;
      }
      const resolvedRejection =
        cause instanceof ApiError &&
        (cause.code === 'APPOINTMENT_STATE_CONFLICT' ||
          (change.status !== 'CANCELLED' && cause.code === 'APPOINTMENT_NOT_ENDED') ||
          (change.status === 'CANCELLED' && cause.code === 'CANCELLATION_DEADLINE_PASSED'));
      const uncertain = accepted || (cause instanceof ApiError && cause.unknownOutcome);
      if (uncertain || resolvedRejection) {
        let latest: StaffAppointmentDetail | null = null;
        try {
          latest = await onReload(controller.signal);
        } catch {
          // Keep the original operation until an authorized detail read succeeds.
        }
        if (controller.signal.aborted) return;
        reconcile(change, latest, cause, resolvedRejection);
      } else {
        if (wasUnknown) staffChanges.markUnknown(change.userId, change.appointmentId);
        else staffChanges.clear(change.userId, change.appointmentId);
        setError(errorMessage(cause));
        if (change.status !== 'CANCELLED') closeOutcome(true);
      }
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setPending(false);
    }
  }

  function confirmChange(status: StaffChangeIntent['status'], reason?: string) {
    if (pending) return;
    if (!expectedUserId) {
      setError(errorMessage(new ApiError('ACCOUNT_CHANGED', 401)));
      return;
    }
    void runChange({ appointmentId: appointment.id, userId: expectedUserId, status, reason });
  }

  async function verifyLatest() {
    if (pending || (!unresolved && !refreshRequired)) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setPending(true);
    setError('');
    try {
      const latest = await onReload(controller.signal);
      if (controller.signal.aborted) return;
      if (unresolved) reconcile(unresolved, latest, new ApiError('TIMEOUT'), false);
      else {
        setRefreshRequired(false);
        setError('');
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setPending(false);
    }
  }

  return (
    <section className={staffStyles.actions} aria-label="Thao tác lịch hẹn">
      {error && !showCancel && !outcomeToConfirm && (
        <p className={staffStyles.error} role="alert">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {(savedChange?.phase === 'unknown' || refreshRequired) && (
        <div className={staffStyles.recovery}>
          <p>
            {unresolved
              ? 'Kết quả thao tác chưa rõ. Giữ nguyên lựa chọn cho đến khi xác minh được lịch.'
              : 'Cần tải lại trạng thái lịch trước khi thao tác tiếp.'}
          </p>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={pending}
            onClick={() => void verifyLatest()}
          >
            Kiểm tra trạng thái
          </button>
          {unresolved && (
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={pending}
              onClick={() => void runChange(unresolved)}
            >
              Thử lại thao tác này
            </button>
          )}
        </div>
      )}
      {cancellable && (
        <button
          ref={cancelTrigger}
          className={styles.secondaryButton}
          type="button"
          disabled={actionsLocked}
          onClick={() => setShowCancel(true)}
        >
          Hủy lịch
        </button>
      )}
      {appointment.status === 'UPCOMING' && (
        <>
          <button
            ref={completeTrigger}
            className={styles.secondaryButton}
            type="button"
            disabled={actionsLocked}
            onClick={() => setOutcomeToConfirm('COMPLETED')}
          >
            Đánh dấu hoàn thành
          </button>
          <button
            ref={noShowTrigger}
            className={styles.secondaryButton}
            type="button"
            disabled={actionsLocked}
            onClick={() => setOutcomeToConfirm('NO_SHOW')}
          >
            Đánh dấu vắng mặt
          </button>
        </>
      )}
      {showCancel && (
        <div className={staffStyles.backdrop}>
          <section
            ref={cancelDialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="cancel-title"
            tabIndex={-1}
            className={staffStyles.dialog}
            onKeyDown={(event) => handleDialogKeyDown(event, () => closeCancel())}
          >
            <h2 id="cancel-title">Xác nhận hủy lịch</h2>
            <p>Thao tác này cập nhật lịch trên hệ thống.</p>
            {error && (
              <p className={staffStyles.error} role="alert">
                {error}
              </p>
            )}
            <label htmlFor="cancel-reason">Lý do hủy (không bắt buộc)</label>
            <textarea
              ref={cancelReason}
              id="cancel-reason"
              maxLength={2000}
              value={reason}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
            />
            <div className={staffStyles.dialogActions}>
              <button
                className={styles.secondaryButton}
                type="button"
                disabled={pending}
                onClick={() => closeCancel()}
              >
                Đóng
              </button>
              <button
                className={styles.primaryButton}
                type="button"
                disabled={pending}
                onClick={() => confirmChange('CANCELLED', reason || undefined)}
              >
                {pending ? 'Đang lưu…' : 'Xác nhận hủy'}
              </button>
            </div>
          </section>
        </div>
      )}
      {outcomeToConfirm && (
        <div className={staffStyles.backdrop}>
          <section
            ref={outcomeDialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="outcome-title"
            tabIndex={-1}
            className={staffStyles.dialog}
            onKeyDown={(event) => handleDialogKeyDown(event, () => closeOutcome())}
          >
            <h2 id="outcome-title">Xác nhận kết quả buổi gặp</h2>
            <p>
              Ghi nhận buổi gặp là {outcomeToConfirm === 'COMPLETED' ? 'đã hoàn thành' : 'vắng mặt'}
              ?
            </p>
            <div className={staffStyles.dialogActions}>
              <button
                className={styles.secondaryButton}
                type="button"
                disabled={pending}
                onClick={() => closeOutcome()}
              >
                Đóng
              </button>
              <button
                ref={outcomeConfirm}
                className={styles.primaryButton}
                type="button"
                disabled={pending}
                onClick={() => confirmChange(outcomeToConfirm)}
              >
                {pending
                  ? 'Đang lưu…'
                  : outcomeToConfirm === 'COMPLETED'
                    ? 'Xác nhận hoàn thành'
                    : 'Xác nhận vắng mặt'}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
