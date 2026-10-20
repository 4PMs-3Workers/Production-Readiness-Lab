'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { useApi, useBookingIntent } from './api-provider';
import { ApiFeedback } from './api-feedback';
import { ApiBookingRecovery } from './api-booking-recovery';
import { ApiPurposeStep, ApiStaffStep, ApiDateStep } from './api-booking-fields';
import { useBookingContext } from './use-booking-context';
import { ApiError, errorMessage } from '@/services/api/errors';
import { formatTimeZoneLabel } from '@/lib/business-time';
import {
  bookingReducer,
  initialBookingState,
  canVisitStep,
  isCompleteSelection,
  type BookingAction,
  type BookingState,
} from '../booking/booking-state';
import { SelectionSummary } from '../booking/booking-steps';
import type { BookingContext } from '@/types/api';
import type { BookingIntent } from '@/services/api/booking-intent';
import type { AvailableSlot } from '@/types/booking';
import styles from '../booking/booking.module.css';

const steps = ['Nhu cầu tư vấn', 'Người tư vấn', 'Ngày & giờ', 'Xác nhận'];

interface OwnedDraft {
  readonly owner: string;
  readonly state: BookingState;
  readonly context?: BookingContext;
  readonly slotRefresh: number;
}

type OwnedEvent =
  | { readonly owner: string; readonly action: BookingAction }
  | { readonly owner: string; readonly context: BookingContext };

function sameContextBounds(left: BookingContext, right: BookingContext): boolean {
  return (
    left.timezone === right.timezone &&
    left.minBookingDate === right.minBookingDate &&
    left.maxBookingDate === right.maxBookingDate
  );
}

function reduceOwnedDraft(current: OwnedDraft | null, event: OwnedEvent): OwnedDraft {
  const sameOwner = current?.owner === event.owner;
  const currentState = sameOwner ? current.state : initialBookingState;
  if ('action' in event) {
    const state = bookingReducer(currentState, event.action);
    return {
      owner: event.owner,
      state,
      ...(sameOwner && current.context ? { context: current.context } : {}),
      slotRefresh:
        (sameOwner ? current.slotRefresh : 0) + (event.action.type === 'bookingConflict' ? 1 : 0),
    };
  }

  const previousContext = sameOwner ? current.context : undefined;
  const contextChanged =
    previousContext !== undefined && !sameContextBounds(previousContext, event.context);
  return {
    owner: event.owner,
    state:
      contextChanged && previousContext
        ? bookingReducer(currentState, {
            type: 'contextChanged',
            previous: previousContext,
            next: event.context,
          })
        : currentState,
    context: event.context,
    slotRefresh: (sameOwner ? current.slotRefresh : 0) + (contextChanged ? 1 : 0),
  };
}

export function ApiBookingFlow() {
  const { services, intents, session, revision } = useApi();
  const intent = useBookingIntent();
  const router = useRouter();
  const userId = session.status === 'authenticated' ? session.user.id : '';
  const loadContext = useCallback(
    (signal: AbortSignal) => services.context({ signal }),
    [services],
  );
  const { state: contextState, retry: retryContext } = useBookingContext(
    userId,
    revision,
    loadContext,
  );
  const owner = userId ? `${revision}:${userId}` : '';
  const [ownedDraft, dispatchOwned] = useReducer(reduceOwnedDraft, null);
  const [{ owner: errorOwner, value: flowError }, setOwnedError] = useState<{
    owner: string;
    value: unknown;
  }>({ owner: '', value: null });
  const selectionState = ownedDraft?.owner === owner ? ownedDraft.state : initialBookingState;
  const slotRefresh = ownedDraft?.owner === owner ? ownedDraft.slotRefresh : 0;
  const currentError = errorOwner === owner ? flowError : null;
  const dispatch = useCallback(
    (action: BookingAction) => dispatchOwned({ owner, action }),
    [owner],
  );
  const setError = useCallback((value: unknown) => setOwnedError({ owner, value }), [owner]);
  const context = contextState.data;
  const contextReady =
    contextState.status === 'success' &&
    ownedDraft?.owner === owner &&
    ownedDraft.context !== undefined &&
    sameContextBounds(ownedDraft.context, contextState.data);
  const activeIntent: BookingIntent | null = intent?.userId === userId ? intent : null;

  useEffect(() => {
    if (contextState.status !== 'success') return;
    dispatchOwned({ owner, context: contextState.data });
  }, [contextState, owner]);

  async function submit() {
    if (!userId || activeIntent?.phase === 'submitting') return;
    if (!activeIntent) {
      if (!contextReady || !isCompleteSelection(selectionState.selection)) return;
      const { selection } = selectionState;
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
        }
        if (['NOT_FOUND', 'VALIDATION_ERROR'].includes(cause.code)) retryContext();
      }
    }
  }

  function startNew() {
    intents.startNew();
    dispatch({ type: 'reset' });
    setError(null);
  }

  if (session.status !== 'authenticated') return null;

  if (activeIntent) {
    return (
      <div className={styles.booking}>
        <ApiBookingRecovery
          intent={activeIntent}
          error={currentError}
          contextLoading={contextState.status === 'loading'}
          contextMessage={contextState.status === 'error' ? contextState.message : undefined}
          onReplay={() => void submit()}
          onRetryContext={retryContext}
          onStartNew={startNew}
        />
      </div>
    );
  }

  if (!context) {
    return (
      <ApiFeedback
        loading={contextState.status === 'loading'}
        message={contextState.status === 'error' ? contextState.message : undefined}
        retry={contextState.status === 'error' ? retryContext : undefined}
      />
    );
  }

  return (
    <>
      {contextState.status === 'error' && (
        <ApiFeedback message={contextState.message} retry={retryContext} />
      )}
      <ApiWizard
        context={context}
        contextReady={contextReady}
        sessionUser={session.user}
        selectionState={selectionState}
        dispatch={dispatch}
        error={currentError}
        setError={setError}
        slotRefresh={slotRefresh}
        onContextRefresh={retryContext}
        onSubmit={() => void submit()}
      />
    </>
  );
}

function ApiWizard({
  context,
  contextReady,
  sessionUser,
  selectionState,
  dispatch,
  error,
  setError,
  slotRefresh,
  onContextRefresh,
  onSubmit,
}: {
  context: BookingContext;
  contextReady: boolean;
  sessionUser: { displayName: string; email: string };
  selectionState: BookingState;
  dispatch: (action: BookingAction) => void;
  error: unknown;
  setError: (value: unknown) => void;
  slotRefresh: number;
  onContextRefresh: () => void;
  onSubmit: () => void;
}) {
  const { selection, step } = selectionState;
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [step]);

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
      <ol className={styles.steps} aria-label="Các bước đặt lịch">
        {steps.map((label, index) => (
          <li
            key={label}
            className={index <= step ? styles.reached : ''}
            aria-current={index === step ? 'step' : undefined}
          >
            <button
              type="button"
              disabled={index >= step}
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
                refresh={slotRefresh}
                onDate={(date) =>
                  dispatch({
                    type: 'dateSelected',
                    purposeId: selection.purpose!.id,
                    staffId: selection.staff!.id,
                    date,
                  })
                }
                onSlot={(slot: AvailableSlot) => dispatch({ type: 'slotSelected', slot })}
                onContextRefresh={onContextRefresh}
              />
            )}
            {step === 3 && (
              <>
                <div className={styles.reviewIdentity}>
                  <span className={styles.reviewAvatar} aria-hidden="true">
                    SV
                  </span>
                  <div>
                    <strong>{sessionUser.displayName}</strong>
                    <span>{sessionUser.email}</span>
                  </div>
                </div>
                <SelectionSummary selection={selection} timezone={context.timezone} />
                <p className={styles.reviewNote}>
                  Giờ trống sẽ được kiểm tra lại khi xác nhận. Lịch hẹn thành công được lưu trong hệ
                  thống.
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
                disabled={!contextReady || !canVisitStep(selection, step + 1)}
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
                disabled={!contextReady}
                onClick={onSubmit}
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
            <div className={styles.summaryFootnote}>{formatTimeZoneLabel(context.timezone)}</div>
          </section>
        </aside>
      </div>
      <p className={styles.requiredNote}>
        Nếu kết nối bị gián đoạn rồi bạn tải lại trang, hãy{' '}
        <Link href="/appointments">kiểm tra lịch đã đặt</Link> trước khi gửi yêu cầu mới.
      </p>
    </div>
  );
}
