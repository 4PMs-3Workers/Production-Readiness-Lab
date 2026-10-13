import Link from 'next/link';
import type { ReactNode } from 'react';

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Đến nội dung chính
      </a>
      <header className="site-header">
        <Link className="brand" href="/login" aria-label="Cổng tư vấn sinh viên — trang đăng nhập">
          <span className="brand-mark" aria-hidden="true">
            TV
          </span>
          <span>
            <strong>Cổng tư vấn</strong>
            <span className="brand-caption">Đồng hành cùng sinh viên</span>
          </span>
        </Link>
        <nav aria-label="Điều hướng chính" className="navigation">
          <Link href="/login">Đăng nhập</Link>
          <Link href="/booking">Đặt lịch</Link>
          <Link href="/booking/result">Kết quả</Link>
        </nav>
      </header>
      <div className="demo-banner">
        <span className="demo-badge">DEMO</span>
        <p>Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.</p>
      </div>
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
      <footer className="site-footer">
        <span>Production Readiness Lab</span>
        <span>Cổng tư vấn sinh viên · Bản demo</span>
      </footer>
    </div>
  );
}
