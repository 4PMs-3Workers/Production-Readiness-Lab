'use client';

import Link from 'next/link';

import { useDemo } from '@/features/demo/demo-provider';
import { formatBusinessDate, formatBusinessTime } from '@/lib/business-time';

import styles from './booking.module.css';
import { isMockMode } from '@/lib/application-mode';
import { ApiHistory } from '@/features/api/api-appointments';

export function BookingHistoryView() {
  return isMockMode() ? <MockBookingHistoryView /> : <ApiHistory />;
}
function MockBookingHistoryView() {
  const { bookings } = useDemo();
  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>CUỘC HẸN CỦA BẠN</p>
          <h1>Lịch đã đặt</h1>
          <p>Các lịch tư vấn đã xác nhận trong phiên demo · Giờ Việt Nam.</p>
        </div>
        <span className={styles.serviceBadge}>{bookings.length} lịch trong phiên</span>
      </div>
      <div className={styles.demoNotice}>
        <span className={styles.demoTag}>DEMO</span>
        <span>
          Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật. Tải lại trang, đặt lại demo hoặc đăng
          xuất sẽ xóa danh sách.
        </span>
      </div>
      {bookings.length ? (
        <>
          <p className={styles.historyCount} role="status">
            Có {bookings.length} lịch đã đặt, hiển thị lần đặt mới nhất trước.
          </p>
          <ol className={styles.historyList} aria-label="Các lịch đã đặt trong phiên">
            {bookings.map((booking) => (
              <li key={booking.id}>
                <article
                  className={styles.historyCard}
                  aria-labelledby={`appointment-${booking.id}`}
                >
                  <header className={styles.historyHeader}>
                    <span className={styles.historyReference}>{booking.reference}</span>
                    <span>Đã xác nhận demo</span>
                  </header>
                  <h2 id={`appointment-${booking.id}`}>{booking.purpose.name}</h2>
                  <dl className={styles.historyDetails}>
                    <div>
                      <dt>Người tư vấn</dt>
                      <dd>{booking.staff.displayName}</dd>
                    </div>
                    <div>
                      <dt>Ngày hẹn</dt>
                      <dd>{formatBusinessDate(booking.slot.date)}</dd>
                    </div>
                    <div>
                      <dt>Giờ hẹn</dt>
                      <dd>
                        {formatBusinessTime(booking.slot.startsAt)} –{' '}
                        {formatBusinessTime(booking.slot.endsAt)} ·{' '}
                        {booking.purpose.durationMinutes} phút
                      </dd>
                    </div>
                    <div>
                      <dt>Địa điểm</dt>
                      <dd>{booking.slot.location ?? 'Địa điểm sẽ được thông báo sau'}</dd>
                    </div>
                  </dl>
                  <div className={styles.historyActions}>
                    <Link
                      className={styles.secondaryButton}
                      href={`/appointments/${encodeURIComponent(booking.id)}`}
                    >
                      Xem chi tiết<span className={styles.srOnly}> lịch {booking.reference}</span>
                      <span aria-hidden="true">→</span>
                    </Link>
                  </div>
                </article>
              </li>
            ))}
          </ol>
          <Link className={styles.primaryButton} href="/booking">
            Đặt thêm lịch tư vấn <span aria-hidden="true">→</span>
          </Link>
        </>
      ) : (
        <section className={styles.resultCard}>
          <div className={styles.emptyState}>
            <h2>Bạn chưa có lịch đã đặt trong phiên.</h2>
            <p>
              Lịch xác nhận thành công sẽ xuất hiện tại đây. Bạn có thể bắt đầu một cuộc hẹn demo
              mới.
            </p>
            <Link className={styles.primaryButton} href="/booking">
              Bắt đầu đặt lịch <span aria-hidden="true">→</span>
            </Link>
          </div>
        </section>
      )}
    </div>
  );
}
