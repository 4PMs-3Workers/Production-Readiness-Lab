'use client';

import Link from 'next/link';
import { useApi } from './api-provider';
import styles from '@/components/portal-shell.module.css';

export function ApiAccount() {
  const { session, logout } = useApi();
  if (session.status !== 'authenticated')
    return (
      <Link className={styles.loginLink} href="/login">
        Đăng nhập ↗
      </Link>
    );
  return (
    <div className={styles.account}>
      <Link href="/home">Trang chủ</Link>
      <button type="button" onClick={() => void logout().catch(() => {})}>
        Đăng xuất
      </button>
    </div>
  );
}
export function ApiProfile() {
  const { session } = useApi();
  if (session.status !== 'authenticated') return null;
  return (
    <div className={styles.profile}>
      <span className={styles.avatar} aria-hidden="true">
        SV
      </span>
      <div>
        <strong>{session.user.displayName}</strong>
        <span>Đã đăng nhập</span>
      </div>
      <span className={styles.onlineDot} aria-hidden="true" />
    </div>
  );
}
