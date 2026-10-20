'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
import { useApiResource } from '@/features/api/use-api-resource';
import { ApiError, errorMessage } from '@/services/api/errors';
import {
  formatBusinessDate,
  formatBusinessTime,
  formatTimeZoneLabel,
  getBusinessDate,
} from '@/lib/business-time';
import { localDateTimeToOffset } from '@/lib/staff-time';
import styles from '@/features/booking/booking.module.css';
import staffStyles from './staff-unavailability.module.css';

interface IntervalBody {
  readonly startsAt: string;
  readonly endsAt: string;
}

export function StaffUnavailabilityManager() {
  const { services, session } = useApi();
  const expectedUserId =
    session.status === 'authenticated' && session.user.role === 'STAFF' ? session.user.id : null;
  const [page, setPage] = useState(1);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [retryCreate, setRetryCreate] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const replayBody = useRef<IntervalBody | null>(null);
  const contextLoad = useCallback(
    (signal: AbortSignal) => services.staffContext({ signal }),
    [services],
  );
  const context = useApiResource('staff-business-context', contextLoad);
  const listLoad = useCallback(
    (signal: AbortSignal) => services.staffUnavailability({ page, pageSize: 20 }, { signal }),
    [services, page],
  );
  const list = useApiResource(`staff-unavailability:${page}`, listLoad);
  useEffect(() => () => activeRequest.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    if (!expectedUserId) {
      setError('Phiên đăng nhập đã thay đổi. Hãy tải lại trang.');
      return;
    }
    setError('');
    setMessage('');
    let body = replayBody.current;
    if (!body) {
      try {
        if (context.state.status !== 'success')
          throw new RangeError('Chưa tải được múi giờ nghiệp vụ.');
        const startsAt = localDateTimeToOffset(start, context.state.data.timezone);
        const endsAt = localDateTimeToOffset(end, context.state.data.timezone);
        if (Date.parse(startsAt) >= Date.parse(endsAt))
          throw new RangeError('Thời điểm kết thúc phải sau thời điểm bắt đầu.');
        body = { startsAt, endsAt };
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Ngày giờ chưa hợp lệ.');
        return;
      }
    }

    const controller = new AbortController();
    activeRequest.current = controller;
    setPending(true);
    try {
      await services.createStaffUnavailability(body.startsAt, body.endsAt, {
        signal: controller.signal,
        expectedUserId,
      });
      if (controller.signal.aborted) return;
      replayBody.current = null;
      setRetryCreate(false);
      setStart('');
      setEnd('');
      setMessage('Đã thêm khoảng thời gian không nhận lịch.');
      setPage(1);
      list.retry();
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.unknownOutcome) {
        replayBody.current = body;
        setRetryCreate(true);
      }
      setError(errorMessage(cause));
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setPending(false);
    }
  }

  async function remove(id: string) {
    if (pending) return;
    if (!expectedUserId) {
      setError('Phiên đăng nhập đã thay đổi. Hãy tải lại trang.');
      return;
    }
    const controller = new AbortController();
    activeRequest.current = controller;
    setPending(true);
    setError('');
    setMessage('');
    try {
      await services.deleteStaffUnavailability(id, { signal: controller.signal, expectedUserId });
      if (controller.signal.aborted) return;
      setMessage('Đã xóa khoảng thời gian không nhận lịch.');
      list.retry();
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.unknownOutcome) {
        // The item may move to another page after refresh. Absence from this page
        // cannot confirm whether the DELETE reached the server.
        setError(
          'Chưa xác nhận được kết quả xóa. Danh sách đang được tải lại; hãy kiểm tra trước khi thao tác tiếp.',
        );
        list.retry();
      } else setError(errorMessage(cause));
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setPending(false);
    }
  }

  if (context.state.status !== 'success')
    return (
      <ApiFeedback
        loading={context.state.status === 'loading'}
        message={context.state.status === 'error' ? context.state.message : undefined}
        retry={context.state.status === 'error' ? context.retry : undefined}
      />
    );
  if (list.state.status !== 'success')
    return (
      <ApiFeedback
        loading={list.state.status === 'loading'}
        message={list.state.status === 'error' ? list.state.message : undefined}
        retry={list.state.status === 'error' ? list.retry : undefined}
      />
    );

  const timezone = context.state.data.timezone;
  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>LỊCH LÀM VIỆC</p>
          <h1>Thời gian không nhận lịch</h1>
          <p>Thời gian nhập và hiển thị theo {formatTimeZoneLabel(timezone)}.</p>
        </div>
      </div>
      <p className={staffStyles.notice}>Lịch hẹn hiện có không bị xóa hoặc thay đổi.</p>
      {error && (
        <p role="alert" className={staffStyles.error}>
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <form className={staffStyles.form} onSubmit={(event) => void submit(event)}>
        <label>
          Bắt đầu
          <input
            type="datetime-local"
            required
            value={start}
            disabled={pending || retryCreate}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>
        <label>
          Kết thúc
          <input
            type="datetime-local"
            required
            value={end}
            disabled={pending || retryCreate}
            onChange={(event) => setEnd(event.target.value)}
          />
        </label>
        <div>
          {retryCreate ? (
            <button className={styles.primaryButton} type="submit" disabled={pending}>
              {pending ? 'Đang gửi…' : 'Thử lại khoảng này'}
            </button>
          ) : (
            <button className={styles.primaryButton} type="submit" disabled={pending}>
              {pending ? 'Đang lưu…' : 'Thêm khoảng thời gian'}
            </button>
          )}
        </div>
      </form>
      <h2>Khoảng thời gian đã đăng ký</h2>
      {list.state.data.items.length === 0 ? (
        <p>Chưa có khoảng thời gian nào.</p>
      ) : (
        <ol className={staffStyles.list} aria-label="Các khoảng không nhận lịch">
          {list.state.data.items.map((item) => (
            <li className={staffStyles.period} key={item.id}>
              <div>
                <strong>
                  {formatBusinessDate(getBusinessDate(item.startsAt, timezone))} ·{' '}
                  {formatBusinessTime(item.startsAt, timezone)} —{' '}
                  {formatBusinessDate(getBusinessDate(item.endsAt, timezone))} ·{' '}
                  {formatBusinessTime(item.endsAt, timezone)}
                </strong>
                <span>{timezone}</span>
              </div>
              <button
                className={styles.secondaryButton}
                type="button"
                disabled={pending}
                aria-label="Xóa khoảng thời gian"
                onClick={() => void remove(item.id)}
              >
                Xóa
              </button>
            </li>
          ))}
        </ol>
      )}
      <Pagination page={list.state.data} onPage={setPage} />
    </div>
  );
}
