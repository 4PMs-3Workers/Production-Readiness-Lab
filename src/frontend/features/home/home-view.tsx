'use client';

import Link from 'next/link';

import { useDemo } from '@/features/demo/demo-provider';

import styles from './home.module.css';
import { isMockMode } from '@/lib/application-mode';
import { ApiHome } from '@/features/api/api-home';

export function HomeView() {
  return isMockMode() ? <MockHomeView /> : <ApiHome />;
}
function MockHomeView() {
  const { student, result, bookings } = useDemo();
  return (
    <div className={styles.home}>
      <div className={styles.heading}>
        <p>KHÔNG GIAN SINH VIÊN</p>
        <h1>
          {student ? `Chào mừng, ${student.displayName}.` : 'Chào mừng đến với cổng sinh viên.'}
        </h1>
        <span>Một nơi để tìm sự hỗ trợ và chủ động sắp xếp cuộc hẹn của bạn.</span>
      </div>
      <div className={styles.notice}>
        <span>DEMO</span>
        <p>Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.</p>
      </div>
      {!student ? (
        <section className={styles.empty} aria-labelledby="session-title">
          <span className={styles.serviceIcon} aria-hidden="true">
            SV
          </span>
          <h2 id="session-title">Bạn chưa có phiên đăng nhập demo.</h2>
          <p>
            Đăng nhập để mở thông tin sinh viên minh họa. Tải lại trang sẽ kết thúc phiên; bạn vẫn
            có thể xem và thử đặt lịch trực tiếp.
          </p>
          <div className={styles.actions}>
            <Link className={styles.primary} href="/login">
              Đăng nhập demo <span aria-hidden="true">→</span>
            </Link>
            <Link className={styles.secondary} href="/booking">
              Xem demo đặt lịch
            </Link>
          </div>
        </section>
      ) : (
        <div className={styles.grid}>
          <section className={styles.service} aria-labelledby="service-title">
            <div className={styles.serviceIcon} aria-hidden="true">
              ▦
            </div>
            <p className={styles.kicker}>DỊCH VỤ SINH VIÊN</p>
            <h2 id="service-title">Đặt lịch tư vấn</h2>
            <p>
              Trao đổi cùng cố vấn về học tập, nghiên cứu, nghề nghiệp và những lựa chọn tiếp theo.
            </p>
            <ol className={styles.steps}>
              <li>Chọn nhu cầu và cố vấn</li>
              <li>Chọn ngày, giờ phù hợp</li>
              <li>Xác nhận và xem kết quả demo</li>
            </ol>
            <Link className={styles.primary} href="/booking">
              Bắt đầu đặt lịch <span aria-hidden="true">→</span>
            </Link>
            {result && (
              <div className={styles.historyLinks}>
                <Link className={styles.resultLink} href="/booking/result">
                  Xem kết quả gần nhất · {result.reference}
                </Link>
                <Link className={styles.resultLink} href="/appointments">
                  Xem tất cả {bookings.length} lịch đã đặt
                </Link>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
