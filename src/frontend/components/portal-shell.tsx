import Image from 'next/image';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { isMockMode } from '@/lib/application-mode';

import { PortalAccount, PortalProfile } from './portal-account';
import styles from './portal-shell.module.css';

export function PortalShell({
  children,
  section = 'booking',
}: {
  children: ReactNode;
  section?: 'home' | 'booking' | 'history' | 'profile';
}) {
  return (
    <div className={styles.portal}>
      <a className="skip-link" href="#main-content">
        Đến nội dung chính
      </a>
      <header className={styles.header}>
        <Link href="/home" aria-label="HUS — Cổng tư vấn sinh viên — Trang chủ">
          <Image
            src="/branding/hus-logo.png"
            alt="HUS — Trường Đại học Khoa học Tự nhiên, Đại học Quốc gia Hà Nội"
            width={661}
            height={99}
            sizes="(max-width: 600px) 275px, 330px"
            preload
            className={styles.logo}
          />
        </Link>
        <div className={styles.headerTitle}>
          <strong>CỔNG THÔNG TIN SINH VIÊN</strong>
          <span>Dịch vụ tư vấn và hỗ trợ sinh viên</span>
        </div>
        <PortalAccount />
      </header>
      <aside className={styles.sidebar}>
        <div className={styles.sidebarHeading}>KHÔNG GIAN SINH VIÊN</div>
        <nav aria-label="Điều hướng chính">
          <Link
            className={`${styles.navLink} ${section === 'profile' ? styles.activeLink : ''}`}
            href="/profile"
            aria-current={section === 'profile' ? 'page' : undefined}
          >
            <span className={styles.calendarIcon} aria-hidden="true">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
              >
                <circle cx="12" cy="8" r="3.5" />
                <path d="M5 21v-2a7 7 0 0 1 14 0v2" />
              </svg>
            </span>
            Thông tin sinh viên
            <span className={styles.chevron} aria-hidden="true">
              ›
            </span>
          </Link>
          <Link
            className={`${styles.navLink} ${section === 'booking' ? styles.activeLink : ''}`}
            href="/booking"
            aria-current={section === 'booking' ? 'page' : undefined}
          >
            <span className={styles.calendarIcon} aria-hidden="true">
              ▦
            </span>
            Đặt lịch tư vấn
            <span className={styles.chevron} aria-hidden="true">
              ›
            </span>
          </Link>
          <Link
            className={`${styles.navLink} ${section === 'history' ? styles.activeLink : ''}`}
            href="/appointments"
            aria-current={section === 'history' ? 'page' : undefined}
          >
            <span className={styles.calendarIcon} aria-hidden="true">
              ☷
            </span>
            Lịch đã đặt
            <span className={styles.chevron} aria-hidden="true">
              ›
            </span>
          </Link>
        </nav>
        <div className={styles.sidebarNote}>
          <span className={styles.noteIcon} aria-hidden="true">
            i
          </span>
          <strong>Luôn có người đồng hành</strong>
          <p>Chọn một cuộc hẹn để trao đổi về học tập, nghiên cứu và định hướng của bạn.</p>
        </div>
        <PortalProfile />
      </aside>
      <div className={styles.workspace}>
        <div className={styles.breadcrumb}>
          <Link href="/home">Trang chủ</Link> <span aria-hidden="true">/</span>{' '}
          <strong>
            {section === 'home'
              ? 'Không gian sinh viên'
              : section === 'history'
                ? 'Lịch đã đặt'
                : section === 'profile'
                  ? 'Thông tin sinh viên'
                  : 'Đặt lịch tư vấn'}
          </strong>
        </div>
        <main id="main-content" tabIndex={-1} className={styles.main}>
          {children}
        </main>
        <footer className={styles.footer}>
          <span>HUS · Cổng tư vấn sinh viên</span>
          <span>Production Readiness Lab{isMockMode() ? ' · Bản demo' : ''}</span>
        </footer>
      </div>
    </div>
  );
}
