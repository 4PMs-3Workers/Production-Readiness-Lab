'use client';

import { useCallback, useState } from 'react';
import { useApi } from './api-provider';
import { useApiResource } from './use-api-resource';
import { ApiFeedback } from './api-feedback';
import {
  addCalendarDays,
  formatBusinessDate,
  formatBusinessTime,
  formatTimeZoneLabel,
} from '@/lib/business-time';
import type { BookingSelection } from '../booking/booking-state';
import type { AvailableSlot, Purpose, Staff } from '@/types/booking';
import type { BookingContext, Page } from '@/types/api';
import styles from '../booking/booking.module.css';

export function Pagination({
  page,
  onPage,
}: {
  page: Page<unknown>;
  onPage: (page: number) => void;
}) {
  return (
    <nav className={styles.pagination} aria-label="Phân trang">
      <button
        type="button"
        className={styles.secondaryButton}
        disabled={page.page <= 1}
        onClick={() => onPage(page.page - 1)}
      >
        Trang trước
      </button>
      <span role="status">
        Trang {page.page} · {page.total} kết quả
      </span>
      <button
        type="button"
        className={styles.secondaryButton}
        disabled={page.page * page.pageSize >= page.total}
        onClick={() => onPage(page.page + 1)}
      >
        Trang sau
      </button>
    </nav>
  );
}

export function ApiPurposeStep({
  selection,
  onSelect,
  onDescription,
}: {
  selection: BookingSelection;
  onSelect: (purpose: Purpose) => void;
  onDescription: (text: string) => void;
}) {
  const { services } = useApi();
  const [page, setPage] = useState(1);
  const load = useCallback(
    (signal: AbortSignal) => services.purposes(page, { signal }),
    [services, page],
  );
  const { state, retry } = useApiResource(`purposes:${page}`, load);
  if (state.status !== 'success')
    return (
      <ApiFeedback
        loading={state.status === 'loading'}
        message={state.status === 'error' ? state.message : undefined}
        retry={state.status === 'error' ? retry : undefined}
      />
    );
  return (
    <>
      {!state.data.items.length && <ApiFeedback />}
      <fieldset className={styles.fieldset}>
        <legend className={styles.srOnly}>Chọn nhu cầu tư vấn</legend>
        <div className={styles.choiceGrid}>
          {state.data.items.map((purpose) => (
            <label
              key={purpose.id}
              className={`${styles.choice} ${selection.purpose?.id === purpose.id ? styles.selected : ''}`}
            >
              <input
                type="radio"
                name="purpose"
                checked={selection.purpose?.id === purpose.id}
                onChange={() => onSelect(purpose)}
              />
              <span className={styles.purposeIcon} aria-hidden="true">
                ▤
              </span>
              <span className={styles.choiceContent}>
                <strong>{purpose.name}</strong>
                <span>
                  {purpose.requiresFreeText
                    ? 'Chia sẻ điều bạn đang cần hỗ trợ'
                    : 'Trao đổi cùng cố vấn'}
                </span>
                <small>{purpose.durationMinutes} phút / buổi</small>
              </span>
              <span className={styles.radioMark} aria-hidden="true" />
            </label>
          ))}
        </div>
      </fieldset>
      <Pagination page={state.data} onPage={setPage} />
      {selection.purpose?.requiresFreeText && (
        <div className={styles.descriptionField}>
          <label htmlFor="api-description">Bạn muốn trao đổi về điều gì? *</label>
          <textarea
            id="api-description"
            value={selection.description}
            onChange={(event) => onDescription(event.target.value)}
            required
            maxLength={2000}
            rows={3}
          />
          <p>Vui lòng nhập nội dung trước khi tiếp tục. Tối đa 2.000 ký tự.</p>
        </div>
      )}
    </>
  );
}

export function ApiStaffStep({
  purpose,
  selected,
  onSelect,
}: {
  purpose: Purpose;
  selected: Staff | null;
  onSelect: (staff: Staff) => void;
}) {
  const { services } = useApi();
  const [page, setPage] = useState(1);
  const load = useCallback(
    (signal: AbortSignal) => services.staff(purpose.id, page, { signal }),
    [services, purpose.id, page],
  );
  const { state, retry } = useApiResource(`staff:${purpose.id}:${page}`, load);
  if (state.status !== 'success')
    return (
      <ApiFeedback
        loading={state.status === 'loading'}
        message={state.status === 'error' ? state.message : undefined}
        retry={state.status === 'error' ? retry : undefined}
      />
    );
  return (
    <>
      {!state.data.items.length && <ApiFeedback />}
      <fieldset className={styles.fieldset}>
        <legend className={styles.srOnly}>Chọn người tư vấn</legend>
        <div className={styles.staffGrid}>
          {state.data.items.map((staff) => (
            <label
              key={staff.id}
              className={`${styles.choice} ${styles.staffChoice} ${selected?.id === staff.id ? styles.selected : ''}`}
            >
              <input
                type="radio"
                name="staff"
                checked={selected?.id === staff.id}
                onChange={() => onSelect(staff)}
              />
              <span className={styles.staffAvatar} aria-hidden="true">
                {staff.displayName.slice(-2)}
              </span>
              <span className={styles.choiceContent}>
                <strong>{staff.displayName}</strong>
                <span>{purpose.name}</span>
                <small>Nhân viên tư vấn</small>
              </span>
              <span className={styles.radioMark} aria-hidden="true" />
            </label>
          ))}
        </div>
      </fieldset>
      <Pagination page={state.data} onPage={setPage} />
    </>
  );
}

function ApiSlots({
  selection,
  timezone,
  onSlot,
  onContextRefresh,
}: {
  selection: BookingSelection & { purpose: Purpose; staff: Staff };
  timezone: string;
  onSlot: (slot: AvailableSlot) => void;
  onContextRefresh: () => void;
}) {
  const { services } = useApi();
  const { purpose, staff, date } = selection;
  const load = useCallback(
    (signal: AbortSignal) =>
      services.slots({ purposeId: purpose.id, staffId: staff.id, date }, { signal }),
    [services, purpose.id, staff.id, date],
  );
  const { state, retry } = useApiResource(`slots:${purpose.id}:${staff.id}:${date}`, load);
  if (state.status !== 'success')
    return (
      <ApiFeedback
        loading={state.status === 'loading'}
        message={state.status === 'error' ? state.message : undefined}
        retry={
          state.status === 'error'
            ? () => {
                retry();
                onContextRefresh();
              }
            : undefined
        }
      />
    );
  if (!state.data.length)
    return (
      <ApiFeedback message="Ngày này chưa có giờ trống phù hợp. Vui lòng chọn một ngày khác." />
    );
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.fieldLabel}>
        Giờ có thể đặt · {purpose.durationMinutes} phút / buổi
      </legend>
      <div className={styles.slotGrid}>
        {state.data.map((slot) => (
          <label
            key={slot.id}
            className={`${styles.slot} ${selection.slot?.id === slot.id ? styles.selected : ''}`}
          >
            <input
              type="radio"
              name="slot"
              checked={selection.slot?.id === slot.id}
              onChange={() => onSlot(slot)}
            />
            <strong>
              {formatBusinessTime(slot.startsAt, timezone)} –{' '}
              {formatBusinessTime(slot.endsAt, timezone)}
            </strong>
            <span>Địa điểm thông báo sau</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function ApiDateStep({
  selection,
  context,
  refresh,
  onDate,
  onSlot,
  onContextRefresh,
}: {
  selection: BookingSelection & { purpose: Purpose; staff: Staff };
  context: BookingContext;
  refresh: number;
  onDate: (date: string) => void;
  onSlot: (slot: AvailableSlot) => void;
  onContextRefresh: () => void;
}) {
  const [datePage, setDatePage] = useState(() =>
    selection.date
      ? Math.max(
          0,
          Math.floor(
            (Date.parse(selection.date) - Date.parse(context.minBookingDate)) / (7 * 86400000),
          ),
        )
      : 0,
  );
  const firstDate = addCalendarDays(context.minBookingDate, datePage * 7);
  const dates = Array.from({ length: 7 }, (_, index) => addCalendarDays(firstDate, index)).filter(
    (date) => date <= context.maxBookingDate,
  );
  return (
    <>
      <fieldset className={styles.fieldset}>
        <legend className={styles.fieldLabel}>
          Chọn ngày hẹn <span>· {formatTimeZoneLabel(context.timezone)}</span>
        </legend>
        <div className={styles.dateGrid}>
          {dates.map((date) => (
            <label
              className={`${styles.date} ${selection.date === date ? styles.selected : ''}`}
              key={date}
            >
              <input
                type="radio"
                name="date"
                value={date}
                aria-label={formatBusinessDate(date)}
                checked={selection.date === date}
                onChange={() => onDate(date)}
              />
              <span>
                {new Intl.DateTimeFormat('vi-VN', { timeZone: 'UTC', weekday: 'short' }).format(
                  new Date(`${date}T12:00:00Z`),
                )}
              </span>
              <strong>{date.slice(-2)}</strong>
              <small>Tháng {Number(date.slice(5, 7))}</small>
            </label>
          ))}
        </div>
        {addCalendarDays(context.minBookingDate, 6) < context.maxBookingDate && (
          <nav className={styles.pagination} aria-label="Chọn tuần">
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={datePage === 0}
              onClick={() => setDatePage((value) => value - 1)}
            >
              Tuần trước
            </button>
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={addCalendarDays(firstDate, 7) > context.maxBookingDate}
              onClick={() => setDatePage((value) => value + 1)}
            >
              Tuần sau
            </button>
          </nav>
        )}
        <p className={styles.requiredNote}>
          Có thể đặt từ {formatBusinessDate(context.minBookingDate)} đến{' '}
          {formatBusinessDate(context.maxBookingDate)}.
        </p>
      </fieldset>
      <div className={styles.slotSection}>
        {selection.date ? (
          <ApiSlots
            key={`${selection.purpose.id}:${selection.staff.id}:${selection.date}:${refresh}`}
            selection={selection}
            timezone={context.timezone}
            onSlot={onSlot}
            onContextRefresh={onContextRefresh}
          />
        ) : (
          <p className={styles.dateHint}>Chọn ngày để xem các giờ tư vấn còn trống.</p>
        )}
      </div>
    </>
  );
}
