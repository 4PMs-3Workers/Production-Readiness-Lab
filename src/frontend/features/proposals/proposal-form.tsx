'use client';

import { useRef, useEffect, useState, type FormEvent } from 'react';
import type { ProposalContext, ProposalCreateBody } from '@/services/proposal-contracts';
import { proposalDateTime, proposalTimestamp } from '@/lib/proposal-time';
import { formatTimeZoneLabel } from '@/lib/business-time';
import { ProposalValues } from './proposal-history';
import { ProposalConfirmation } from './proposal-confirmation';
import booking from '@/features/booking/booking.module.css';
import styles from './proposals.module.css';

export interface ProposalAppointment {
  readonly id: string;
  readonly startsAt: string;
  readonly endsAt: string;
  readonly location: string | null;
  readonly durationMinutes: number;
  readonly status: string;
}

export function ProposalForm({
  appointment,
  context,
  timezone,
  busy,
  operationError,
  onSubmit,
  onClose,
}: {
  appointment: ProposalAppointment;
  context: ProposalContext;
  timezone: string;
  busy: boolean;
  operationError?: string;
  onSubmit: (body: ProposalCreateBody) => void;
  onClose: () => void;
}) {
  const original = proposalDateTime(appointment.startsAt, timezone);
  const [date, setDate] = useState(original.date);
  const [time, setTime] = useState(original.time);
  const [locationMode, setLocationMode] = useState<'keep' | 'change' | 'clear'>('keep');
  const [location, setLocation] = useState('');
  const [error, setError] = useState('');
  const [review, setReview] = useState<ProposalCreateBody | null>(null);
  const dateInput = useRef<HTMLInputElement>(null);
  useEffect(() => dateInput.current?.focus(), []);

  function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError('');
    try {
      if (locationMode === 'change' && (!location.trim() || [...location].length > 500))
        throw new RangeError('Nhập địa điểm từ 1 đến 500 ký tự, không chỉ gồm khoảng trắng.');
      const body: ProposalCreateBody = {
        expected_proposal_version: context.proposalVersion,
        expected_pending_proposal_id: context.pendingProposalId,
        proposed_starts_at: proposalTimestamp(date, time, timezone, appointment.startsAt),
        proposed_location:
          locationMode === 'keep'
            ? appointment.location
            : locationMode === 'clear'
              ? null
              : location,
      };
      if (
        body.proposed_starts_at === appointment.startsAt &&
        body.proposed_location === appointment.location
      )
        throw new RangeError('Hãy thay đổi thời gian hoặc địa điểm trước khi gửi đề xuất.');
      setReview(Object.freeze(body));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Thông tin đề xuất chưa hợp lệ.');
    }
  }

  return (
    <>
      <form className={styles.form} onSubmit={prepare} aria-label="Soạn đề xuất đổi lịch">
        <h3>{context.pendingProposalId ? 'Thay thế đề xuất đang chờ' : 'Soạn đề xuất mới'}</h3>
        <p>
          Nhập theo {formatTimeZoneLabel(timezone)}. Hệ thống kiểm tra thời gian và khả dụng khi
          gửi; lịch gốc tiếp tục giữ chỗ trong khi chờ sinh viên phản hồi.
        </p>
        <fieldset disabled={busy || review !== null}>
          <legend className={booking.srOnly}>Thời gian và địa điểm đề xuất</legend>
          <div className={styles.fields}>
            <label>
              Ngày đề xuất
              <input
                ref={dateInput}
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <label>
              Giờ đề xuất
              <input type="time" required value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
            <label>
              Thay đổi địa điểm
              <select
                value={locationMode}
                onChange={(e) => setLocationMode(e.target.value as typeof locationMode)}
              >
                <option value="keep">Giữ nguyên địa điểm</option>
                <option value="change">Đổi địa điểm</option>
                <option value="clear">Bỏ địa điểm</option>
              </select>
            </label>
            {locationMode === 'change' && (
              <label>
                Địa điểm đề xuất
                <input
                  required
                  maxLength={1000}
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                />
              </label>
            )}
          </div>
        </fieldset>
        {error && <p role="alert">{error}</p>}
        <div className={booking.pagination}>
          <button
            className={booking.primaryButton}
            type="submit"
            disabled={busy || review !== null}
          >
            Xem lại đề xuất
          </button>
          <button
            className={booking.secondaryButton}
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Đóng biểu mẫu
          </button>
        </div>
      </form>
      {review && (
        <ProposalConfirmation
          title={
            context.pendingProposalId ? 'Xác nhận thay thế đề xuất?' : 'Xác nhận đề xuất đổi lịch?'
          }
          confirmLabel="Xác nhận gửi đề xuất"
          busy={busy}
          onClose={() => setReview(null)}
          onConfirm={() => onSubmit(review)}
        >
          <div className={styles.comparison}>
            <div>
              <h3>Lịch gốc đang giữ chỗ</h3>
              <ProposalValues {...appointment} timezone={timezone} />
            </div>
            <div>
              <h3>Thay đổi đề xuất</h3>
              <ProposalValues
                startsAt={review.proposed_starts_at}
                endsAt={new Date(
                  Date.parse(review.proposed_starts_at) + appointment.durationMinutes * 60_000,
                ).toISOString()}
                location={review.proposed_location}
                durationMinutes={appointment.durationMinutes}
                timezone={timezone}
              />
            </div>
          </div>
          {context.pendingProposalId && (
            <p>Đề xuất đang chờ sẽ được thay thế. Sinh viên sẽ phản hồi đề xuất mới.</p>
          )}
          {operationError && <p role="alert">{operationError}</p>}
        </ProposalConfirmation>
      )}
    </>
  );
}
