'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
import { useApiResource } from '@/features/api/use-api-resource';
import { ApiError, errorMessage } from '@/services/api/errors';
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
  const { services, session } = useApi();
  const expectedUserId =
    session.status === 'authenticated' && session.user.role === 'STAFF' ? session.user.id : null;
  const [showCancel, setShowCancel] = useState(false);
  const [outcomeToConfirm, setOutcomeToConfirm] = useState<StaffOutcomeStatus | null>(null);
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const activeRequest = useRef<AbortController | null>(null);
  const cancelTrigger = useRef<HTMLButtonElement>(null);
  const cancelReason = useRef<HTMLTextAreaElement>(null);
  const completeTrigger = useRef<HTMLButtonElement>(null);
  const noShowTrigger = useRef<HTMLButtonElement>(null);
  const outcomeConfirm = useRef<HTMLButtonElement>(null);
  const cancellable = appointment.status === 'UPCOMING' || appointment.status === 'CHANGE_PENDING';
  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    if (showCancel) cancelReason.current?.focus();
  }, [showCancel]);
  useEffect(() => {
    if (outcomeToConfirm) outcomeConfirm.current?.focus();
  }, [outcomeToConfirm]);

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
    if (!controls?.length) return;
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

  async function confirmChange(
    action: (signal: AbortSignal) => Promise<unknown>,
    expected: StaffAppointmentStatus,
  ) {
    if (pending) return;
    if (!expectedUserId) {
      setError(errorMessage(new ApiError('ACCOUNT_CHANGED', 401)));
      return;
    }
    const controller = new AbortController();
    activeRequest.current = controller;
    setPending(true);
    setError('');
    setMessage('');
    try {
      await action(controller.signal);
      const latest = await onReload(controller.signal);
      if (latest.status === expected) {
        setMessage(expected === 'CANCELLED' ? 'Đã hủy lịch' : 'Đã cập nhật kết quả');
        if (expected === 'CANCELLED') closeCancel(true);
        else closeOutcome(true);
        setReason('');
      } else {
        setError('Chưa xác nhận được thay đổi. Hãy tải lại chi tiết lịch.');
        if (expected !== 'CANCELLED') closeOutcome(true);
      }
    } catch (cause) {
      let latest: StaffAppointmentDetail | null = null;
      if (cause instanceof ApiError && cause.unknownOutcome) {
        try {
          latest = await onReload(controller.signal);
        } catch {
          latest = null;
        }
      }
      if (latest?.status === expected) {
        setMessage(expected === 'CANCELLED' ? 'Đã hủy lịch' : 'Đã cập nhật kết quả');
        if (expected === 'CANCELLED') closeCancel(true);
        else closeOutcome(true);
        setReason('');
      } else {
        setError(errorMessage(cause));
        if (expected !== 'CANCELLED') closeOutcome(true);
      }
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
      {cancellable && (
        <button
          ref={cancelTrigger}
          className={styles.secondaryButton}
          type="button"
          disabled={pending}
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
            disabled={pending}
            onClick={() => setOutcomeToConfirm('COMPLETED')}
          >
            Đánh dấu hoàn thành
          </button>
          <button
            ref={noShowTrigger}
            className={styles.secondaryButton}
            type="button"
            disabled={pending}
            onClick={() => setOutcomeToConfirm('NO_SHOW')}
          >
            Đánh dấu vắng mặt
          </button>
        </>
      )}
      {showCancel && (
        <div className={staffStyles.backdrop}>
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="cancel-title"
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
                onClick={() =>
                  void confirmChange(
                    (signal) =>
                      services.cancelStaffAppointment(appointment.id, reason || undefined, {
                        signal,
                        expectedUserId: expectedUserId!,
                      }),
                    'CANCELLED',
                  )
                }
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
            role="dialog"
            aria-modal="true"
            aria-labelledby="outcome-title"
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
                onClick={() =>
                  void confirmChange(
                    (signal) =>
                      services.recordStaffOutcome(appointment.id, outcomeToConfirm, {
                        signal,
                        expectedUserId: expectedUserId!,
                      }),
                    outcomeToConfirm,
                  )
                }
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
