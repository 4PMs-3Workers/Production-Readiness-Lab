'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { useDemo } from '@/features/demo/demo-provider';

import styles from './portal-shell.module.css';

export function PortalAccount() {
  const { student, logout } = useDemo();
  const router = useRouter();
  if (!student)
    return (
      <Link className={styles.loginLink} href="/login">
        Đăng nhập <span aria-hidden="true">↗</span>
      </Link>
    );
  return (
    <div className={styles.account}>
      <Link href="/home">Trang chủ</Link>
      <button
        type="button"
        onClick={() => {
          logout();
          router.replace('/login');
        }}
      >
        Đăng xuất
      </button>
    </div>
  );
}

export function PortalProfile() {
  const { student } = useDemo();
  return (
    <div className={styles.profile}>
      <span className={styles.avatar} aria-hidden="true">
        SV
      </span>
      <div>
        <strong>{student?.displayName ?? 'Sinh viên demo'}</strong>
        <span>{student ? 'Đã đăng nhập demo' : 'Phiên trải nghiệm'}</span>
      </div>
      <span className={styles.onlineDot} aria-hidden="true" />
    </div>
  );
}
