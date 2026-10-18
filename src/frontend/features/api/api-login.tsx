'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useApi } from './api-provider';
import { safeReturnPath } from './session-gate';
import { errorMessage } from '@/services/api/errors';
import styles from '../login/login.module.css';

export function ApiLogin() {
  const { session, login, refreshSession, logout } = useApi();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const alert = useRef<HTMLDivElement>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (error) alert.current?.focus();
  }, [error]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    const submitted = password;
    setPassword('');
    try {
      await login({ email: email.trim(), password: submitted }, { signal: controller.signal });
      if (!controller.signal.aborted) router.replace(safeReturnPath(params.get('returnTo')));
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  }
  if (session.status === 'loading' || (session.status === 'authenticated' && session.verifying))
    return <p role="status">Đang kiểm tra phiên đăng nhập…</p>;
  if (session.status === 'error')
    return (
      <div role="alert">
        <p>{errorMessage(session.error)}</p>
        <button type="button" onClick={() => void refreshSession()}>
          Kiểm tra lại phiên
        </button>
      </div>
    );
  if (session.status === 'authenticated')
    return (
      <div className={styles.signedIn}>
        <span className="card-kicker">PHIÊN ĐĂNG NHẬP</span>
        <h2>Chào bạn, {session.user.displayName}.</h2>
        <p>
          {session.user.role === 'STUDENT'
            ? 'Bạn có thể tiếp tục sử dụng dịch vụ tư vấn.'
            : 'Cổng này dành cho sinh viên. Vai trò của bạn chưa được hỗ trợ.'}
        </p>
        {session.user.role === 'STUDENT' && (
          <Link className="primary-link" href={safeReturnPath(params.get('returnTo'))}>
            Vào cổng sinh viên →
          </Link>
        )}
        <button
          type="button"
          onClick={() => void logout().catch((cause: unknown) => setError(errorMessage(cause)))}
        >
          Đăng xuất
        </button>
        {error && <p role="alert">{error}</p>}
      </div>
    );
  return (
    <>
      <span className="card-kicker">01 / ĐĂNG NHẬP</span>
      <h2>Đăng nhập</h2>
      <p className={styles.description}>
        Sử dụng tài khoản sinh viên để đặt và quản lý lịch tư vấn.
      </p>
      <form className={styles.form} onSubmit={submit} aria-label="Đăng nhập" aria-busy={busy}>
        {error && (
          <div role="alert" tabIndex={-1} ref={alert} className={styles.error}>
            {error}
          </div>
        )}
        <fieldset disabled={busy}>
          <div className={styles.field}>
            <label htmlFor="api-email">Email</label>
            <input
              id="api-email"
              type="email"
              autoComplete="username"
              required
              maxLength={254}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="api-password">Mật khẩu</label>
            <input
              id="api-password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={1024}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button className={styles.submit} type="submit">
            {busy ? 'Đang đăng nhập…' : 'Đăng nhập'} <span aria-hidden="true">→</span>
          </button>
        </fieldset>
        <p className={styles.sessionNote} role="status">
          {busy
            ? 'Đang xác thực tài khoản…'
            : 'Lịch hẹn được lưu trong hệ thống và có thể xem lại sau khi tải lại trang.'}
        </p>
      </form>
      <details className={styles.bookingGuide}>
        <summary>Cách đặt lịch tư vấn</summary>
        <ol>
          <li>Đăng nhập bằng tài khoản sinh viên.</li>
          <li>Chọn nhu cầu tư vấn và người tư vấn phù hợp.</li>
          <li>Chọn ngày và khung giờ còn trống.</li>
          <li>Kiểm tra thông tin, xác nhận và xem lại trong mục Lịch đã đặt.</li>
        </ol>
      </details>
    </>
  );
}
