'use client';

import { useCallback, useEffect, useRef } from 'react';

import { useDemo } from '@/features/demo/demo-provider';
import { formatBusinessDate, formatBusinessTime } from '@/lib/business-time';
import type { AvailableSlot, Purpose, Staff } from '@/types/booking';

import styles from './booking.module.css';
import type { BookingSelection } from './booking-state';
import { useDemoResource } from './use-demo-resource';

function Feedback({
  loading,
  error,
  retry,
  empty,
}: {
  loading?: boolean;
  error?: string;
  retry?: () => void;
  empty?: string;
}) {
  const feedback = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) feedback.current?.focus();
  }, [error]);
  return (
    <div
      className={styles.feedback}
      role={error ? 'alert' : 'status'}
      tabIndex={error ? -1 : undefined}
      ref={feedback}
    >
      {loading ? (
        <>
          <span className={styles.spinner} aria-hidden="true" />
          Đang tải dữ liệu…
        </>
      ) : (
        <>
          <strong>{error ? 'Chưa thể tải dữ liệu' : 'Chưa có lựa chọn phù hợp'}</strong>
          <p>{error || empty}</p>
          {retry && (
            <button className={styles.secondaryButton} type="button" onClick={retry}>
              Thử lại
            </button>
          )}
        </>
      )}
    </div>
  );
}

export function PurposeStep({
  selection,
  onSelect,
  onDescription,
}: {
  selection: BookingSelection;
  onSelect: (purpose: Purpose) => void;
  onDescription: (text: string) => void;
}) {
  const { services } = useDemo();
  const load = useCallback((signal: AbortSignal) => services.listPurposes({ signal }), [services]);
  const { state, retry } = useDemoResource('purposes', load);
  if (state.status === 'loading') return <Feedback loading />;
  if (state.status === 'error') return <Feedback error={state.message} retry={retry} />;
  if (!state.data.length)
    return <Feedback empty="Hiện chưa có nhu cầu tư vấn. Bạn có thể quay lại sau." />;
  return (
    <>
      <fieldset className={styles.fieldset}>
        <legend className={styles.srOnly}>Chọn nhu cầu tư vấn</legend>
        <div className={styles.choiceGrid}>
          {state.data.map((purpose, index) => (
            <label
              className={`${styles.choice} ${selection.purpose?.id === purpose.id ? styles.selected : ''}`}
              key={purpose.id}
            >
              <input
                type="radio"
                name="purpose"
                value={purpose.id}
                checked={selection.purpose?.id === purpose.id}
                onChange={() => onSelect(purpose)}
              />
              <span className={styles.purposeIcon} aria-hidden="true">
                {['↗', '⌘', '▤', '☷', '⋯'][index]}
              </span>
              <span className={styles.choiceContent}>
                <strong>{purpose.name}</strong>
                <span>
                  {purpose.requiresFreeText
                    ? 'Chia sẻ điều bạn đang cần hỗ trợ'
                    : [
                        'Cơ hội nghề nghiệp và hướng đi tiếp theo',
                        'Đề tài, phương pháp và định hướng nghiên cứu',
                        'Lộ trình học tập và kế hoạch cá nhân',
                        'Trao đổi những băn khoăn của bạn',
                      ][index]}
                </span>
                <small>{purpose.durationMinutes} phút / buổi</small>
              </span>
              <span className={styles.radioMark} aria-hidden="true" />
            </label>
          ))}
        </div>
      </fieldset>
      {selection.purpose?.requiresFreeText && (
        <div className={styles.descriptionField}>
          <label htmlFor="booking-description">
            Bạn muốn trao đổi về điều gì? <span>*</span>
          </label>
          <textarea
            id="booking-description"
            value={selection.description}
            onChange={(event) => onDescription(event.target.value)}
            placeholder="Mô tả ngắn để cố vấn có thể chuẩn bị tốt hơn…"
            rows={3}
            required
            aria-describedby="description-help"
          />
          <p id="description-help">Vui lòng nhập nội dung trước khi tiếp tục.</p>
        </div>
      )}
    </>
  );
}

export function StaffStep({
  purpose,
  selected,
  onSelect,
}: {
  purpose: Purpose;
  selected: Staff | null;
  onSelect: (staff: Staff) => void;
}) {
  const { services } = useDemo();
  const load = useCallback(
    (signal: AbortSignal) => services.listStaff(purpose.id, { signal }),
    [services, purpose.id],
  );
  const { state, retry } = useDemoResource(`staff:${purpose.id}`, load);
  if (state.status === 'loading') return <Feedback loading />;
  if (state.status === 'error') return <Feedback error={state.message} retry={retry} />;
  if (!state.data.length)
    return (
      <Feedback empty="Chưa có cố vấn tiếp nhận nhu cầu này. Bạn có thể quay lại để chọn nhu cầu khác." />
    );
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.srOnly}>Chọn người tư vấn</legend>
      <div className={styles.staffGrid}>
        {state.data.map((staff) => (
          <label
            className={`${styles.choice} ${styles.staffChoice} ${selected?.id === staff.id ? styles.selected : ''}`}
            key={staff.id}
          >
            <input
              type="radio"
              name="staff"
              value={staff.id}
              checked={selected?.id === staff.id}
              onChange={() => onSelect(staff)}
            />
            <span className={styles.staffAvatar} aria-hidden="true">
              {staff.displayName.slice(-2)}
            </span>
            <span className={styles.choiceContent}>
              <strong>{staff.displayName}</strong>
              <span>{purpose.name}</span>
              <small>Nhân viên tư vấn · Dữ liệu demo</small>
            </span>
            <span className={styles.radioMark} aria-hidden="true" />
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function SlotOptions({
  purpose,
  staff,
  date,
  selected,
  onSelect,
}: {
  purpose: Purpose;
  staff: Staff;
  date: string;
  selected: AvailableSlot | null;
  onSelect: (slot: AvailableSlot) => void;
}) {
  const { services } = useDemo();
  const load = useCallback(
    (signal: AbortSignal) =>
      services.listAvailableSlots({ purposeId: purpose.id, staffId: staff.id, date }, { signal }),
    [services, purpose.id, staff.id, date],
  );
  const { state, retry } = useDemoResource(`slots:${purpose.id}:${staff.id}:${date}`, load);
  if (state.status === 'loading') return <Feedback loading />;
  if (state.status === 'error') return <Feedback error={state.message} retry={retry} />;
  if (!state.data.length)
    return (
      <Feedback empty="Ngày này chưa có giờ trống phù hợp với lịch của bạn. Vui lòng chọn một ngày khác." />
    );
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.fieldLabel}>
        Giờ có thể đặt <span>· {purpose.durationMinutes} phút / buổi</span>
      </legend>
      <div className={styles.slotGrid}>
        {state.data.map((slot) => (
          <label
            key={slot.id}
            className={`${styles.slot} ${selected?.id === slot.id ? styles.selected : ''}`}
          >
            <input
              type="radio"
              name="slot"
              value={slot.id}
              checked={selected?.id === slot.id}
              onChange={() => onSelect(slot)}
            />
            <strong>
              {formatBusinessTime(slot.startsAt)} – {formatBusinessTime(slot.endsAt)}
            </strong>
            <span>{slot.location ?? 'Địa điểm thông báo sau'}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function DateStep({
  selection,
  refresh,
  onDate,
  onSlot,
}: {
  selection: BookingSelection & { purpose: Purpose; staff: Staff };
  refresh: number;
  onDate: (date: string) => void;
  onSlot: (slot: AvailableSlot) => void;
}) {
  const { services } = useDemo();
  const dates = services.getConfiguration().bookingDates;
  return (
    <>
      <fieldset className={styles.fieldset}>
        <legend className={styles.fieldLabel}>
          Chọn ngày hẹn <span>· Giờ Việt Nam (UTC+7)</span>
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
                checked={selection.date === date}
                onChange={() => onDate(date)}
              />
              <span>
                {new Intl.DateTimeFormat('vi-VN', {
                  timeZone: 'Asia/Ho_Chi_Minh',
                  weekday: 'short',
                }).format(new Date(`${date}T00:00:00+07:00`))}
              </span>
              <strong>{date.slice(-2)}</strong>
              <small>Tháng {Number(date.slice(5, 7))}</small>
            </label>
          ))}
        </div>
        <p className={styles.requiredNote}>Giờ trùng với lịch bạn đã đặt trong phiên sẽ được ẩn.</p>
      </fieldset>
      <div className={styles.slotSection}>
        {selection.date ? (
          <SlotOptions
            key={`${selection.date}:${refresh}`}
            purpose={selection.purpose}
            staff={selection.staff}
            date={selection.date}
            selected={selection.slot}
            onSelect={onSlot}
          />
        ) : (
          <div className={styles.dateHint}>
            Chọn một ngày phía trên để xem các giờ tư vấn còn trống.
          </div>
        )}
      </div>
    </>
  );
}

export function SelectionSummary({ selection }: { selection: BookingSelection }) {
  return (
    <dl className={styles.summaryList}>
      <div>
        <dt>Nhu cầu tư vấn</dt>
        <dd>{selection.purpose?.name ?? 'Chưa chọn nhu cầu'}</dd>
      </div>
      <div>
        <dt>Người tư vấn</dt>
        <dd>{selection.staff?.displayName ?? 'Chưa chọn người tư vấn'}</dd>
      </div>
      <div className={styles.summaryPair}>
        <div>
          <dt>Ngày hẹn</dt>
          <dd>{selection.date ? formatBusinessDate(selection.date) : 'Chưa chọn'}</dd>
        </div>
        <div>
          <dt>Thời lượng</dt>
          <dd>{selection.purpose ? `${selection.purpose.durationMinutes} phút` : '—'}</dd>
        </div>
      </div>
      <div>
        <dt>Giờ hẹn</dt>
        <dd>
          {selection.slot
            ? `${formatBusinessTime(selection.slot.startsAt)} – ${formatBusinessTime(selection.slot.endsAt)}`
            : 'Chưa chọn giờ'}
        </dd>
      </div>
      {selection.slot && (
        <div>
          <dt>Địa điểm</dt>
          <dd>{selection.slot.location ?? 'Địa điểm sẽ được thông báo sau'}</dd>
        </div>
      )}
      {selection.description.trim() && (
        <div>
          <dt>Nội dung trao đổi</dt>
          <dd className={styles.freeText}>{selection.description.trim()}</dd>
        </div>
      )}
    </dl>
  );
}
