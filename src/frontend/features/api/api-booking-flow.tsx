'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { useApi, useBookingIntent } from './api-provider';
import { ApiFeedback } from './api-feedback';
import { ApiPurposeStep, ApiStaffStep, ApiDateStep } from './api-booking-fields';
import { useApiResource } from './use-api-resource';
import { ApiError, errorMessage } from '@/services/api/errors';
import { formatTimeZoneLabel } from '@/lib/business-time';
import {
  bookingReducer,
  initialBookingState,
  canVisitStep,
  isCompleteSelection,
} from '../booking/booking-state';
import { SelectionSummary } from '../booking/booking-steps';
import type { BookingContext } from '@/types/api';
import styles from '../booking/booking.module.css';

const steps = ['Nhu cầu tư vấn', 'Người tư vấn', 'Ngày & giờ', 'Xác nhận'];

export function ApiBookingFlow() {
  const { services } = useApi();
  const [notice, setNotice] = useState('');
  const load = useCallback((signal: AbortSignal) => services.context({ signal }), [services]);
  const { state, retry } = useApiResource('booking-context', load);
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
      {notice && <ApiFeedback message={notice} />}
      <ApiWizard
        key={`${state.data.minBookingDate}:${state.data.maxBookingDate}:${state.data.timezone}`}
        context={state.data}
        refreshContext={(message) => {
          if (message) setNotice(message);
          retry();
        }}
      />
    </>
  );
}

function ApiWizard({
  context,
  refreshContext,
}: {
  context: BookingContext;
  refreshContext: (message?: string) => void;
}) {
  const { services, intents, session } = useApi();
  const intent = useBookingIntent();
  const router = useRouter();
  const [{ selection, step }, dispatch] = useReducer(bookingReducer, initialBookingState);
  const [error, setError] = useState<unknown>(null);
  const [refresh, setRefresh] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [step]);
  if (session.status !== 'authenticated') return null;
  const userId = session.user.id;
  const locked = intent !== null;
  async function submit() {
    if (intent?.phase === 'submitting') return;
    if (!intent) {
      if (!isCompleteSelection(selection)) return;
      intents.prepare(userId, {
        purpose_id: selection.purpose.id,
        staff_id: selection.staff.id,
        starts_at: selection.slot.startsAt,
        ...(selection.purpose.requiresFreeText ? { description: selection.description } : {}),
      });
    }
    setError(null);
    try {
      const receipt = await intents.submit(services, userId);
      router.push(`/booking/result?id=${encodeURIComponent(receipt.id)}`);
    } catch (cause) {
      if (cause instanceof Error && cause.name === 'AbortError') return;
      setError(cause);
      if (cause instanceof ApiError) {
        if (cause.code === 'BOOKING_CONFLICT') {
          dispatch({ type: 'bookingConflict' });
          setRefresh((value) => value + 1);
        }
        if (['NOT_FOUND', 'VALIDATION_ERROR'].includes(cause.code))
          refreshContext(errorMessage(cause));
      }
    }
  }

  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>TƯ VẤN & ĐỒNG HÀNH</p>
          <h1>Đặt lịch tư vấn</h1>
          <p>Chọn một cuộc hẹn, cùng tìm hướng đi phù hợp với bạn.</p>
        </div>
        <span className={styles.serviceBadge}>Dịch vụ sinh viên</span>
      </div>
      {intent ? (
        <section className={styles.formCard}>
          <div className={styles.formBody}>
            <h2>
              {intent.phase === 'confirmed'
                ? 'Lịch hẹn đã được ghi nhận'
                : intent.phase === 'conflict'
                  ? 'Cần kiểm tra mã yêu cầu'
                  : 'Đang xác minh yêu cầu đặt lịch'}
            </h2>
            {intent.phase === 'submitting' ? (
              <p role="status">Đang xác nhận…</p>
            ) : intent.phase === 'confirmed' ? (
              <>
                <p>Thông tin hiện tại sẽ được lấy từ chi tiết lịch hẹn.</p>
                <Link
                  className={styles.primaryButton}
                  href={`/booking/result?id=${encodeURIComponent(intent.receipt!.id)}`}
                >
                  Xem lịch hẹn
                </Link>
                <button
                  className={styles.secondaryButton}
                  type="button"
                  onClick={() => intents.startNew()}
                >
                  Đặt lịch khác
                </button>
              </>
            ) : (
              <>
                <p>
                  Chưa thể xác định kết quả cuối cùng. Hãy kiểm tra lịch sử hoặc gửi lại đúng yêu
                  cầu đã lưu; không cần đặt thêm một lịch mới.
                </p>
                {intent.phase !== 'conflict' && (
                  <button
                    className={styles.primaryButton}
                    type="button"
                    onClick={() => void submit()}
                  >
                    Thử lại cùng yêu cầu
                  </button>
                )}
              </>
            )}
            {error !== null && <ApiFeedback message={errorMessage(error)} />}
            <p>
              <Link href="/appointments">Kiểm tra lịch đã đặt</Link>
            </p>
          </div>
        </section>
      ) : (
        <>
          <ol className={styles.steps} aria-label="Các bước đặt lịch">
            {steps.map((label, index) => (
              <li
                key={label}
                className={index <= step ? styles.reached : ''}
                aria-current={index === step ? 'step' : undefined}
              >
                <button
                  type="button"
                  disabled={index >= step || locked}
                  onClick={() => {
                    setError(null);
                    dispatch({ type: 'stepChanged', step: index });
                  }}
                >
                  <span className={styles.stepNumber}>{index < step ? '✓' : `0${index + 1}`}</span>
                  <span>{label}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className={styles.contentGrid}>
            <section className={styles.formCard} aria-labelledby="api-step-title">
              <header className={styles.cardHeader}>
                <div>
                  <span className={styles.sectionNumber}>BƯỚC 0{step + 1}</span>
                  <h2 id="api-step-title" ref={heading} tabIndex={-1}>
                    {step === 3 ? 'Kiểm tra và xác nhận' : `Chọn ${steps[step].toLowerCase()}`}
                  </h2>
                </div>
                <span className={styles.stepCounter}>{step + 1} / 4</span>
              </header>
              <div className={styles.formBody}>
                {error !== null && (
                  <>
                    <ApiFeedback message={errorMessage(error)} />
                    {error instanceof ApiError && error.code === 'BOOKING_QUOTA_EXCEEDED' && (
                      <Link href="/appointments">Xem lịch đã đặt để quản lý hạn mức</Link>
                    )}
                  </>
                )}
                {step === 0 && (
                  <ApiPurposeStep
                    selection={selection}
                    onSelect={(purpose) => dispatch({ type: 'purposeSelected', purpose })}
                    onDescription={(description) =>
                      dispatch({ type: 'descriptionChanged', description })
                    }
                  />
                )}
                {step === 1 && selection.purpose && (
                  <ApiStaffStep
                    key={selection.purpose.id}
                    purpose={selection.purpose}
                    selected={selection.staff}
                    onSelect={(staff) =>
                      dispatch({ type: 'staffSelected', purposeId: selection.purpose!.id, staff })
                    }
                  />
                )}
                {step === 2 && selection.purpose && selection.staff && (
                  <ApiDateStep
                    selection={{ ...selection, purpose: selection.purpose, staff: selection.staff }}
                    context={context}
                    refresh={refresh}
                    onContextRefresh={refreshContext}
                    onDate={(date) =>
                      dispatch({
                        type: 'dateSelected',
                        purposeId: selection.purpose!.id,
                        staffId: selection.staff!.id,
                        date,
                      })
                    }
                    onSlot={(slot) => dispatch({ type: 'slotSelected', slot })}
                  />
                )}
                {step === 3 && (
                  <>
                    <div className={styles.reviewIdentity}>
                      <span className={styles.reviewAvatar} aria-hidden="true">
                        SV
                      </span>
                      <div>
                        <strong>{session.user.displayName}</strong>
                        <span>{session.user.email}</span>
                      </div>
                    </div>
                    <SelectionSummary selection={selection} timezone={context.timezone} />
                    <p className={styles.reviewNote}>
                      Giờ trống sẽ được kiểm tra lại khi xác nhận. Lịch hẹn thành công được lưu
                      trong hệ thống.
                    </p>
                  </>
                )}
              </div>
              <footer className={styles.actions}>
                {step > 0 ? (
                  <button
                    className={styles.secondaryButton}
                    type="button"
                    onClick={() => {
                      setError(null);
                      dispatch({ type: 'stepChanged', step: step - 1 });
                    }}
                  >
                    ← Quay lại
                  </button>
                ) : (
                  <span className={styles.requiredNote}>Chọn nhu cầu để bắt đầu</span>
                )}
                {step < 3 ? (
                  <button
                    className={styles.primaryButton}
                    type="button"
                    disabled={!canVisitStep(selection, step + 1)}
                    onClick={() => {
                      setError(null);
                      dispatch({ type: 'stepChanged', step: step + 1 });
                    }}
                  >
                    Tiếp tục →
                  </button>
                ) : (
                  <button
                    className={styles.primaryButton}
                    type="button"
                    onClick={() => void submit()}
                  >
                    Xác nhận đặt lịch ✓
                  </button>
                )}
              </footer>
            </section>
            <aside className={styles.summaryColumn}>
              <section className={styles.summaryCard}>
                <div className={styles.summaryHeader}>
                  <span aria-hidden="true">▦</span>
                  <h2>Cuộc hẹn của bạn</h2>
                </div>
                <SelectionSummary selection={selection} timezone={context.timezone} />
                <div className={styles.summaryFootnote}>
                  {formatTimeZoneLabel(context.timezone)}
                </div>
              </section>
            </aside>
          </div>
        </>
      )}
      <p className={styles.requiredNote}>
        Nếu kết nối bị gián đoạn rồi bạn tải lại trang, hãy{' '}
        <Link href="/appointments">kiểm tra lịch đã đặt</Link> trước khi gửi yêu cầu mới.
      </p>
    </div>
  );
}
