'use client';

import Link from 'next/link';

import { useDemo } from '@/features/demo/demo-provider';

import { SelectionSummary } from './booking-steps';
import styles from './booking.module.css';

export function BookingResultView() {
  const { result } = useDemo();
  return (
    <div className={styles.booking}>
      <div className={styles.demoNotice}>
        <span className={styles.demoTag}>DEMO</span>
        <span>Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.</span>
      </div>
      <section className={styles.resultCard}>
        {result ? (
          <>
            <header className={styles.resultHeading}>
              <span className={styles.successIcon} aria-hidden="true">
                ✓
              </span>
              <h1>Đặt lịch demo thành công</h1>
              <p>Thông tin cuộc hẹn của bạn đã được ghi nhận trong phiên trải nghiệm.</p>
              <span className={styles.reference}>Mã lịch hẹn: {result.reference}</span>
            </header>
            <div className={styles.resultBody}>
              <SelectionSummary
                selection={{
                  purpose: result.purpose,
                  staff: result.staff,
                  date: result.slot.date,
                  slot: result.slot,
                  description: result.description ?? '',
                }}
              />
              <p className={styles.reviewNote}>
                Đây là kết quả giả lập. Chưa có lịch hẹn thật nào được tạo.
              </p>
            </div>
            <div className={styles.resultActions}>
              <Link href="/booking" className={styles.primaryButton}>
                Đặt lịch khác <span aria-hidden="true">→</span>
              </Link>
            </div>
          </>
        ) : (
          <div className={styles.emptyState}>
            <span className={styles.successIcon} aria-hidden="true">
              ▦
            </span>
            <h1>Bạn chưa có kết quả đặt lịch.</h1>
            <p>Hãy chọn nhu cầu, cố vấn và giờ hẹn để bắt đầu trải nghiệm.</p>
            <Link href="/booking" className={styles.primaryButton}>
              Quay lại đặt lịch <span aria-hidden="true">→</span>
            </Link>
          </div>
        )}
      </section>
    </div>
  );
}
