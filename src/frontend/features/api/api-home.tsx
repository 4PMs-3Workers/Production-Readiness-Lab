'use client';

import Link from 'next/link';
import { useApi } from './api-provider';
import styles from '../home/home.module.css';

export function ApiHome() {
  const { session } = useApi();
  if (session.status !== 'authenticated') return null;
  return (
    <div className={styles.home}>
      <div className={styles.heading}>
        <p>KHÔNG GIAN SINH VIÊN</p>
        <h1>Chào mừng, {session.user.displayName}.</h1>
        <span>Chủ động sắp xếp cuộc hẹn và tìm sự hỗ trợ phù hợp.</span>
      </div>
      <div className={styles.grid}>
        <section className={styles.service}>
          <div className={styles.serviceIcon} aria-hidden="true">
            ▦
          </div>
          <p className={styles.kicker}>DỊCH VỤ SINH VIÊN</p>
          <h2>Đặt lịch tư vấn</h2>
          <p>Trao đổi cùng cố vấn về học tập, nghiên cứu và nghề nghiệp.</p>
          <ol className={styles.steps}>
            <li>Chọn nhu cầu và cố vấn</li>
            <li>Chọn ngày, giờ phù hợp</li>
            <li>Xác nhận cuộc hẹn</li>
          </ol>
          <Link className={styles.primary} href="/booking">
            Bắt đầu đặt lịch →
          </Link>
          <div className={styles.historyLinks}>
            <Link className={styles.resultLink} href="/appointments">
              Xem lịch đã đặt
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
