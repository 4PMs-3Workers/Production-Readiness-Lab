'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useCallback, useRef, useState } from 'react';
import { useApi } from './api-provider';
import { ApiFeedback } from './api-feedback';
import { Pagination } from './api-booking-fields';
import { useApiResource } from './use-api-resource';
import {
  UTC_TIMEZONE_FALLBACK_MESSAGE,
  useAppointmentTimezone,
  type AppointmentTimezoneState,
} from './use-appointment-timezone';
import { ApiError, errorMessage } from '@/services/api/errors';
import {
  formatBusinessDate,
  formatBusinessTime,
  formatTimeZoneLabel,
  getBusinessDate,
} from '@/lib/business-time';
import { SelectionSummary } from '../booking/booking-steps';
import type { Appointment, AppointmentDetail, AppointmentStatus } from '@/types/api';
import styles from '../booking/booking.module.css';

export const STATUS_LABELS: Record<AppointmentStatus, string> = {
  UPCOMING: 'Sắp diễn ra',
  CHANGE_PENDING: 'Chờ phản hồi đổi lịch',
  CANCELLED: 'Đã hủy',
  COMPLETED: 'Đã hoàn thành',
  NO_SHOW: 'Vắng mặt',
};

function AppointmentSummary({
  appointment,
  timezone,
}: {
  appointment: Appointment;
  timezone: string;
}) {
  return (
    <dl className={styles.historyDetails}>
      <div>
        <dt>Người tư vấn</dt>
        <dd>{appointment.staff.displayName}</dd>
      </div>
      <div>
        <dt>Ngày hẹn</dt>
        <dd>{formatBusinessDate(getBusinessDate(appointment.startsAt, timezone))}</dd>
      </div>
      <div>
        <dt>Giờ hẹn</dt>
        <dd>
          {formatBusinessTime(appointment.startsAt, timezone)} –{' '}
          {formatBusinessTime(appointment.endsAt, timezone)} · {appointment.purpose.durationMinutes}{' '}
          phút
        </dd>
      </div>
      <div>
        <dt>Địa điểm</dt>
        <dd>{appointment.location ?? 'Địa điểm sẽ được thông báo sau'}</dd>
      </div>
    </dl>
  );
}

function TimezoneNotice({ state, retry }: { state: AppointmentTimezoneState; retry: () => void }) {
  if (state.status === 'fallback')
    return (
      <p role="status">
        {UTC_TIMEZONE_FALLBACK_MESSAGE}{' '}
        <button className={styles.secondaryButton} type="button" onClick={retry}>
          Thử lại múi giờ
        </button>
      </p>
    );
  if (state.status === 'error') return <ApiFeedback message={state.message} retry={retry} />;
  if (state.status === 'loading')
    return (
      <p className={styles.reviewNote} role="status">
        Đang kiểm tra múi giờ dịch vụ; hiện đang hiển thị UTC.
      </p>
    );
  return null;
}

export function ApiHistory() {
  const { services } = useApi();
  const timezone = useAppointmentTimezone();
  const [filter, setFilter] = useState<AppointmentStatus | ''>('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async (signal: AbortSignal) => {
      return services.appointments(page, filter || undefined, { signal });
    },
    [services, page, filter],
  );
  const { state, retry } = useApiResource(`history:${page}:${filter}`, load);
  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>CUỘC HẸN CỦA BẠN</p>
          <h1>Lịch đã đặt</h1>
          <p>Lịch hẹn được lưu trong hệ thống, kể cả sau khi tải lại trang.</p>
        </div>
      </div>
      <label htmlFor="appointment-status">Trạng thái lịch hẹn </label>
      <select
        id="appointment-status"
        value={filter}
        onChange={(event) => {
          setFilter(event.target.value as AppointmentStatus | '');
          setPage(1);
        }}
      >
        <option value="">Tất cả</option>
        {Object.entries(STATUS_LABELS).map(([status, label]) => (
          <option key={status} value={status}>
            {label}
          </option>
        ))}
      </select>
      {state.status !== 'success' ? (
        <ApiFeedback
          loading={state.status === 'loading'}
          message={state.status === 'error' ? state.message : undefined}
          retry={state.status === 'error' ? retry : undefined}
        />
      ) : (
        <>
          <p className={styles.historyCount} role="status">
            {state.data.total} lịch hẹn ·{' '}
            {timezone.state.status === 'ready'
              ? timezone.state.timezone === 'UTC'
                ? 'UTC'
                : formatTimeZoneLabel(timezone.state.timezone)
              : 'UTC'}
          </p>
          <TimezoneNotice state={timezone.state} retry={timezone.retry} />
          {!state.data.items.length && (
            <section className={styles.resultCard}>
              <div className={styles.emptyState}>
                <h2>Chưa có lịch hẹn phù hợp.</h2>
                <p>Bạn có thể đổi bộ lọc hoặc bắt đầu đặt lịch tư vấn.</p>
              </div>
            </section>
          )}
          <ol className={styles.historyList} aria-label="Các lịch đã đặt">
            {state.data.items.map((appointment) => (
              <li key={appointment.id}>
                <article className={styles.historyCard}>
                  <header className={styles.historyHeader}>
                    <span className={styles.historyReference}>{appointment.id}</span>
                    <span>{STATUS_LABELS[appointment.status]}</span>
                  </header>
                  <h2>{appointment.purpose.name}</h2>
                  <AppointmentSummary
                    appointment={appointment}
                    timezone={timezone.state.timezone}
                  />
                  <div className={styles.historyActions}>
                    <Link
                      className={styles.secondaryButton}
                      href={`/appointments/${encodeURIComponent(appointment.id)}`}
                    >
                      Xem chi tiết<span className={styles.srOnly}> lịch {appointment.id}</span> →
                    </Link>
                  </div>
                </article>
              </li>
            ))}
          </ol>
          <Pagination page={state.data} onPage={setPage} />
        </>
      )}
      <Link className={styles.primaryButton} href="/booking">
        Đặt lịch tư vấn →
      </Link>
    </div>
  );
}

export function ApiResult({ bookingId }: { bookingId?: string }) {
  const search = useSearchParams();
  const id = bookingId ?? search.get('id');
  if (!id)
    return (
      <div className={styles.booking}>
        <section className={styles.resultCard}>
          <div className={styles.emptyState}>
            <h1>Bạn chưa có kết quả đặt lịch.</h1>
            <p>
              Chưa có mã lịch hẹn để tra cứu. Nếu vừa gặp gián đoạn kết nối, hãy kiểm tra lịch đã
              đặt trước khi gửi yêu cầu mới.
            </p>
            <Link className={styles.primaryButton} href="/appointments">
              Kiểm tra lịch đã đặt
            </Link>
            <Link className={styles.emptyHistoryLink} href="/booking">
              Quay lại đặt lịch
            </Link>
          </div>
        </section>
      </div>
    );
  return <ApiDetail key={id} id={id} />;
}

function ApiDetail({ id }: { id: string }) {
  const { services, intents } = useApi();
  const timezone = useAppointmentTimezone();
  const [notice, setNotice] = useState('');
  const load = useCallback(
    async (signal: AbortSignal) => {
      return services.detail(id, { signal });
    },
    [services, id],
  );
  const { state, retry } = useApiResource(`detail:${id}`, load);
  if (state.status === 'loading') return <ApiFeedback loading />;
  if (state.status === 'error')
    return (
      <div className={styles.booking}>
        <section className={styles.resultCard}>
          <div className={styles.emptyState}>
            <h1>
              {state.error instanceof ApiError && state.error.code === 'NOT_FOUND'
                ? 'Không tìm thấy lịch hẹn.'
                : 'Chưa thể tải chi tiết lịch hẹn.'}
            </h1>
            <ApiFeedback message={state.message} retry={retry} />
            <p>Mã lịch hẹn: {id}. Tra cứu lại sẽ không tạo thêm lịch.</p>
            <Link href="/appointments">Về danh sách lịch đã đặt</Link>
          </div>
        </section>
      </div>
    );
  const appointment = state.data;
  const date = getBusinessDate(appointment.startsAt, timezone.state.timezone);
  return (
    <div className={styles.booking}>
      <section className={styles.resultCard}>
        <header className={styles.resultHeading}>
          <span className={styles.successIcon} aria-hidden="true">
            ▦
          </span>
          <h1>Chi tiết lịch hẹn</h1>
          <p role="status">{STATUS_LABELS[appointment.status]}</p>
          <span className={styles.reference}>Mã lịch hẹn: {appointment.id}</span>
        </header>
        <div className={styles.resultBody}>
          {notice && <p role="status">{notice}</p>}
          <SelectionSummary
            timezone={timezone.state.timezone}
            selection={{
              purpose: appointment.purpose,
              staff: appointment.staff,
              date,
              slot: {
                id: appointment.id,
                purposeId: appointment.purpose.id,
                staffId: appointment.staff.id,
                date,
                startsAt: appointment.startsAt,
                endsAt: appointment.endsAt,
                location: appointment.location,
              },
              description: appointment.description ?? '',
            }}
          />
          <p className={styles.reviewNote}>
            {timezone.state.status === 'ready' && timezone.state.timezone !== 'UTC'
              ? formatTimeZoneLabel(timezone.state.timezone)
              : 'UTC'}
          </p>
          <TimezoneNotice state={timezone.state} retry={timezone.retry} />
          {appointment.cancelledAt && (
            <p>
              Thời điểm hủy:{' '}
              {formatBusinessDate(
                getBusinessDate(appointment.cancelledAt, timezone.state.timezone),
              )}{' '}
              {formatBusinessTime(appointment.cancelledAt, timezone.state.timezone)}
            </p>
          )}
          {appointment.cancelReason && (
            <p className={styles.freeText}>Lý do hủy: {appointment.cancelReason}</p>
          )}
          {(appointment.status === 'UPCOMING' || appointment.status === 'CHANGE_PENDING') && (
            <CancelAppointment
              appointment={appointment}
              onRefresh={(message) => {
                setNotice(message);
                retry();
              }}
            />
          )}
        </div>
        <div className={styles.resultActions}>
          <Link href="/booking" className={styles.primaryButton} onClick={() => intents.startNew()}>
            Đặt lịch khác →
          </Link>
          <Link href="/appointments" className={styles.secondaryButton}>
            Về danh sách lịch đã đặt
          </Link>
        </div>
      </section>
    </div>
  );
}

function CancelAppointment({
  appointment,
  onRefresh,
}: {
  appointment: AppointmentDetail;
  onRefresh: (message: string) => void;
}) {
  const { services } = useApi();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  async function cancel() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      await services.cancel(appointment.id, reason || undefined);
      onRefresh('Lịch hẹn đã được hủy.');
    } catch (cause) {
      if (cause instanceof Error && cause.name === 'AbortError') return;
      const message =
        cause instanceof ApiError && cause.unknownOutcome
          ? 'Chưa rõ kết quả hủy. Đang kiểm tra lại trạng thái; nếu vẫn chưa hủy, bạn có thể gửi lại yêu cầu.'
          : errorMessage(cause);
      setError(message);
      onRefresh(message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <div className={styles.cancelSection}>
      {!open ? (
        <button className={styles.secondaryButton} type="button" onClick={() => setOpen(true)}>
          Hủy lịch hẹn
        </button>
      ) : (
        <section aria-labelledby="cancel-title">
          <h2 id="cancel-title">Xác nhận hủy lịch hẹn?</h2>
          <p>
            Thời hạn hủy là trước 00:00 ngày hẹn theo múi giờ dịch vụ. Hệ thống sẽ kiểm tra điều
            kiện hiện tại.
          </p>
          <div className={styles.descriptionField}>
            <label htmlFor="cancel-reason">Lý do hủy (không bắt buộc)</label>
            <textarea
              id="cancel-reason"
              maxLength={2000}
              rows={3}
              disabled={busy}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          {error && <p role="alert">{error}</p>}
          <div className={styles.pagination}>
            <button
              className={styles.primaryButton}
              type="button"
              disabled={busy}
              onClick={() => void cancel()}
            >
              {busy ? 'Đang hủy…' : 'Xác nhận hủy lịch'}
            </button>
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Giữ lịch hẹn
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
