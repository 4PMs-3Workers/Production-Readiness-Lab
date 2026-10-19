'use client';

import { useEffect, useReducer, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import { useDemo } from '@/features/demo/demo-provider';
import { formatBusinessDate, getBusinessDate } from '@/lib/business-time';
import { DEMO_SCENARIOS, DemoServiceError, isRequestCancelled } from '@/services';
import type { DemoScenarioId } from '@/types/demo';

import { DateStep, PurposeStep, SelectionSummary, StaffStep } from './booking-steps';
import {
  bookingReducer,
  canVisitStep,
  initialBookingState,
  isCompleteSelection,
} from './booking-state';
import styles from './booking.module.css';
import { isMockMode } from '@/lib/application-mode';
import { ApiBookingFlow } from '@/features/api/api-booking-flow';

const steps = ['Nhu cầu tư vấn', 'Người tư vấn', 'Ngày & giờ', 'Xác nhận'];
const descriptions = [
  'Chọn nội dung bạn muốn trao đổi cùng cố vấn.',
  'Những cố vấn dưới đây đang tiếp nhận nhu cầu của bạn.',
  'Chọn ngày và khung giờ thuận tiện cho bạn.',
  'Kiểm tra thông tin trước khi xác nhận cuộc hẹn demo.',
];

export function BookingFlow() {
  return isMockMode() ? <MockBookingFlow /> : <ApiBookingFlow />;
}
function MockBookingFlow() {
  const { revision } = useDemo();
  return <BookingWizard key={revision} />;
}

function BookingWizard() {
  const { services, student, recordBooking, busy, beginSubmission, finishSubmission, resetDemo } =
    useDemo();
  const router = useRouter();
  const [{ selection, step }, dispatch] = useReducer(bookingReducer, initialBookingState);
  const [error, setError] = useState<{ message: string } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const pending = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorAlert = useRef<HTMLDivElement>(null);
  const previousStep = useRef(0);
  const configuration = services.getConfiguration();
  const currentStudent = student ?? services.getDemoStudent();

  useEffect(
    () => () => {
      const controller = pending.current;
      if (!controller) return;
      pending.current = null;
      controller.abort();
      finishSubmission(controller);
    },
    [finishSubmission],
  );
  useEffect(() => {
    if (error) errorAlert.current?.focus();
    else if (previousStep.current !== step) heading.current?.focus();
    previousStep.current = step;
  }, [step, error]);

  const canContinue = canVisitStep(selection, step + 1);

  function changeStep(next: number) {
    if (busy) return;
    setError(null);
    dispatch({ type: 'stepChanged', step: next });
  }

  async function confirmBooking() {
    if (pending.current || !isCompleteSelection(selection)) return;
    const controller = new AbortController();
    pending.current = controller;
    beginSubmission(controller);
    setError(null);
    try {
      const result = await services.createBooking(
        {
          studentId: currentStudent.id,
          purposeId: selection.purpose.id,
          staffId: selection.staff.id,
          date: selection.date,
          slotId: selection.slot.id,
          description: selection.description,
        },
        { signal: controller.signal },
      );
      if (!controller.signal.aborted && pending.current === controller) {
        recordBooking(result);
        router.push('/booking/result');
      }
    } catch (error: unknown) {
      if (controller.signal.aborted || pending.current !== controller || isRequestCancelled(error))
        return;
      setError({
        message: error instanceof Error ? error.message : 'Chưa thể đặt lịch. Vui lòng thử lại.',
      });
      if (error instanceof DemoServiceError && error.code === 'BOOKING_CONFLICT') {
        dispatch({ type: 'bookingConflict' });
        setRefresh((value) => value + 1);
      }
    } finally {
      if (pending.current === controller) pending.current = null;
      finishSubmission(controller);
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
        <span className={styles.serviceBadge}>
          <span aria-hidden="true">▦</span>Dịch vụ sinh viên
        </span>
      </div>
      <div className={styles.demoNotice}>
        <span className={styles.demoTag}>DEMO</span>
        <span>Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.</span>
        <span className={styles.demoDate}>
          Ngày demo: {formatBusinessDate(getBusinessDate(configuration.now))}
        </span>
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
              disabled={index >= step || busy}
              onClick={() => changeStep(index)}
            >
              <span className={styles.stepNumber}>{index < step ? '✓' : `0${index + 1}`}</span>
              <span>{label}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className={styles.contentGrid}>
        <section className={styles.formCard} aria-labelledby="booking-step-title">
          <header className={styles.cardHeader}>
            <div>
              <span className={styles.sectionNumber}>BƯỚC 0{step + 1}</span>
              <h2 id="booking-step-title" tabIndex={-1} ref={heading}>
                {step === 3 ? 'Kiểm tra và xác nhận' : `Chọn ${steps[step].toLowerCase()}`}
              </h2>
              <p>{descriptions[step]}</p>
            </div>
            <span className={styles.stepCounter}>{step + 1} / 4</span>
          </header>
          <div className={styles.formBody}>
            {error && (
              <div role="alert" tabIndex={-1} ref={errorAlert} className={styles.error}>
                <strong>Chưa thể xác nhận lịch hẹn</strong>
                <p>{error.message}</p>
              </div>
            )}
            {step === 0 && (
              <PurposeStep
                selection={selection}
                onSelect={(purpose) => dispatch({ type: 'purposeSelected', purpose })}
                onDescription={(description) =>
                  dispatch({ type: 'descriptionChanged', description })
                }
              />
            )}
            {step === 1 && selection.purpose && (
              <StaffStep
                purpose={selection.purpose}
                selected={selection.staff}
                onSelect={(staff) =>
                  dispatch({ type: 'staffSelected', purposeId: selection.purpose?.id ?? '', staff })
                }
              />
            )}
            {step === 2 && selection.purpose && selection.staff && (
              <DateStep
                selection={{ ...selection, purpose: selection.purpose, staff: selection.staff }}
                refresh={refresh}
                onDate={(date) =>
                  dispatch({
                    type: 'dateSelected',
                    purposeId: selection.purpose?.id ?? '',
                    staffId: selection.staff?.id ?? '',
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
                    <strong>{currentStudent.displayName}</strong>
                    <span>{currentStudent.email}</span>
                  </div>
                  <span className={styles.readyBadge}>Sẵn sàng xác nhận</span>
                </div>
                <SelectionSummary selection={selection} />
                <p className={styles.reviewNote}>
                  Thông tin dưới đây chỉ dùng để trải nghiệm. Xác nhận không tạo lịch hẹn thật.
                </p>
              </>
            )}
          </div>
          <footer className={styles.actions}>
            {step > 0 ? (
              <button
                type="button"
                className={styles.secondaryButton}
                disabled={busy}
                onClick={() => changeStep(step - 1)}
              >
                ← Quay lại
              </button>
            ) : (
              <span className={styles.requiredNote}>Chọn nhu cầu để bắt đầu</span>
            )}
            {step < 3 ? (
              <button
                type="button"
                className={styles.primaryButton}
                disabled={busy || !canContinue}
                onClick={() => changeStep(step + 1)}
              >
                Tiếp tục <span aria-hidden="true">→</span>
              </button>
            ) : (
              <button
                type="button"
                className={styles.primaryButton}
                disabled={busy}
                onClick={confirmBooking}
              >
                {busy ? (
                  <>
                    <span className={styles.spinner} aria-hidden="true" />
                    Đang xác nhận…
                  </>
                ) : (
                  <>
                    Xác nhận đặt lịch <span aria-hidden="true">✓</span>
                  </>
                )}
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
            <SelectionSummary selection={selection} />
            <div className={styles.summaryFootnote}>
              <span aria-hidden="true">◷</span>Thời gian hiển thị theo giờ Việt Nam
            </div>
          </section>
          <section className={styles.tips}>
            <strong>Một cuộc hẹn hữu ích bắt đầu từ sự chuẩn bị.</strong>
            <p>Ghi lại những điều bạn muốn hỏi để thời gian trao đổi cùng cố vấn trọn vẹn hơn.</p>
            <div>
              <span aria-hidden="true">✓</span> Chọn đúng nhu cầu cần hỗ trợ
            </div>
            <div>
              <span aria-hidden="true">✓</span> Kiểm tra ngày và giờ trước khi đặt
            </div>
          </section>
        </aside>
      </div>
      <details className={styles.demoControls}>
        <summary>
          Thiết lập bản demo{' '}
          <span>
            {DEMO_SCENARIOS.find((scenario) => scenario.id === configuration.scenario)?.label}
          </span>
        </summary>
        <div>
          <label htmlFor="demo-scenario">Kịch bản trải nghiệm</label>
          <select
            id="demo-scenario"
            value={configuration.scenario}
            disabled={busy}
            onChange={(event) => resetDemo(event.target.value as DemoScenarioId)}
          >
            {DEMO_SCENARIOS.map((scenario) => (
              <option value={scenario.id} key={scenario.id}>
                {scenario.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={busy}
            onClick={() => resetDemo()}
          >
            Đặt lại demo
          </button>
          <p>
            Đổi kịch bản hoặc đặt lại demo sẽ xóa lựa chọn và các lịch trong phiên. Ngày giả lập cố
            định giúp bạn kiểm tra lại cùng một tình huống.
          </p>
        </div>
      </details>
    </div>
  );
}
