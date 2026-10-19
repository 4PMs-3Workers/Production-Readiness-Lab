'use client';

import Link from 'next/link';
import { ApiFeedback } from './api-feedback';
import type { BookingIntent } from '@/services/api/booking-intent';
import { errorMessage } from '@/services/api/errors';
import styles from '../booking/booking.module.css';

export function ApiBookingRecovery({
  intent,
  error,
  contextLoading,
  contextMessage,
  onReplay,
  onRetryContext,
  onStartNew,
}: {
  intent: BookingIntent;
  error: unknown;
  contextLoading: boolean;
  contextMessage?: string;
  onReplay: () => void;
  onRetryContext: () => void;
  onStartNew: () => void;
}) {
  return (
    <section className={styles.formCard} aria-labelledby="booking-recovery-title">
      <div className={styles.formBody}>
        <h1 id="booking-recovery-title">
          {intent.phase === 'confirmed'
            ? 'Lịch hẹn đã được ghi nhận'
            : intent.phase === 'conflict'
              ? 'Cần kiểm tra mã yêu cầu'
              : 'Đang xác minh yêu cầu đặt lịch'}
        </h1>
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
            <button className={styles.secondaryButton} type="button" onClick={onStartNew}>
              Đặt lịch khác
            </button>
          </>
        ) : (
          <>
            <p>
              Chưa thể xác định kết quả cuối cùng. Hãy kiểm tra lịch sử hoặc gửi lại đúng yêu cầu đã
              lưu; không cần đặt thêm một lịch mới.
            </p>
            {intent.phase !== 'conflict' && (
              <button className={styles.primaryButton} type="button" onClick={onReplay}>
                {intent.phase === 'ready' ? 'Gửi yêu cầu đã lưu' : 'Thử lại cùng yêu cầu'}
              </button>
            )}
          </>
        )}
        {contextMessage ? (
          <ApiFeedback message={contextMessage} retry={onRetryContext} />
        ) : contextLoading ? (
          <ApiFeedback loading />
        ) : null}
        {error !== null && <ApiFeedback message={errorMessage(error)} />}
        <p>
          <Link href="/appointments">Kiểm tra lịch đã đặt</Link>
        </p>
      </div>
    </section>
  );
}
