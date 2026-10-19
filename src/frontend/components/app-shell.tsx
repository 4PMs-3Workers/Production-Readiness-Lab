import Image from 'next/image';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { isMockMode } from '@/lib/application-mode';

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Đến nội dung chính
      </a>
      <header className="site-header">
        <Link
          className="brand"
          href="/login"
          aria-label="HUS — Cổng tư vấn sinh viên — trang đăng nhập"
        >
          <Image
            className="brand-logo"
            src="/branding/hus-logo.png"
            alt="HUS — Trường Đại học Khoa học Tự nhiên, Đại học Quốc gia Hà Nội"
            width={661}
            height={99}
            sizes="(max-width: 370px) calc(100vw - 40px), 330px"
            preload
          />
          <span className="brand-copy">
            <strong>Cổng tư vấn</strong>
            <span className="brand-caption">Đồng hành cùng sinh viên</span>
          </span>
        </Link>
        {isMockMode() && (
          <nav aria-label="Điều hướng chính" className="navigation">
            <Link href="/login">Đăng nhập</Link>
            <Link href="/booking">Đặt lịch</Link>
            <Link href="/booking/result">Kết quả</Link>
          </nav>
        )}
      </header>
      {isMockMode() && (
        <div className="demo-banner">
          <span className="demo-badge">DEMO</span>
          <p>Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.</p>
        </div>
      )}
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
      <footer className="site-footer">
        <span>Production Readiness Lab</span>
        <span>Cổng tư vấn sinh viên{isMockMode() ? ' · Bản demo' : ''}</span>
      </footer>
    </div>
  );
}
