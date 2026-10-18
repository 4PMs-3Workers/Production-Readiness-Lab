'use client';

import Link from 'next/link';
import { useEffect, useRef } from 'react';

import { useDemo } from '@/features/demo/demo-provider';

import { SelectionSummary } from './booking-steps';
import styles from './booking.module.css';
import { isMockMode } from '@/lib/application-mode';
import { ApiResult } from '@/features/api/api-appointments';

export function BookingResultView({ bookingId }: { bookingId?: string } = {}) {
  return isMockMode() ? (
    <MockBookingResultView bookingId={bookingId} />
  ) : (
    <ApiResult bookingId={bookingId} />
  );
}
function MockBookingResultView({ bookingId }: { bookingId?: string }) {
  const { result: latest, bookings } = useDemo();
  const result =
    bookingId === undefined
      ? latest
      : (bookings.find((booking) => booking.id === bookingId) ?? null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (result) heading.current?.focus();
  }, [result]);
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
              <h1 ref={heading} tabIndex={-1}>
                {bookingId === undefined ? 'Đặt lịch demo thành công' : 'Chi tiết lịch đã đặt'}
              </h1>
              <p>Thông tin cuộc hẹn của bạn đã được ghi nhận trong phiên trải nghiệm.</p>
              <span className={styles.reference}>Mã lịch hẹn: {result.reference}</span>
            </header>
            <div className={styles.resultBody}>
              <div className={styles.reviewIdentity}>
                <span className={styles.reviewAvatar} aria-hidden="true">
                  SV
                </span>
                <div>
                  <strong>{result.student.displayName}</strong>
                  <span>{result.student.email}</span>
                </div>
              </div>
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
              <Link href="/appointments" className={styles.secondaryButton}>
                {bookingId === undefined ? 'Xem các lịch đã đặt' : 'Về danh sách lịch đã đặt'}
              </Link>
            </div>
          </>
        ) : (
          <div className={styles.emptyState}>
            <span className={styles.successIcon} aria-hidden="true">
              ▦
            </span>
            <h1>
              {bookingId === undefined
                ? 'Bạn chưa có kết quả đặt lịch.'
                : 'Không tìm thấy lịch hẹn trong phiên demo.'}
            </h1>
            <p>
              {bookingId === undefined
                ? 'Hãy chọn nhu cầu, cố vấn và giờ hẹn để bắt đầu trải nghiệm.'
                : 'Lịch có thể thuộc phiên đã kết thúc. Tải lại trang, đặt lại demo hoặc đăng xuất sẽ xóa lịch trong phiên.'}
            </p>
            <Link href="/booking" className={styles.primaryButton}>
              Quay lại đặt lịch <span aria-hidden="true">→</span>
            </Link>
            <Link href="/appointments" className={styles.emptyHistoryLink}>
              Về danh sách lịch đã đặt
            </Link>
          </div>
        )}
      </section>
    </div>
  );
}
