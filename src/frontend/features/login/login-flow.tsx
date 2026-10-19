'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { useDemo } from '@/features/demo/demo-provider';
import { DEMO_SCENARIOS, isRequestCancelled } from '@/services';
import type { DemoScenarioId } from '@/types/demo';

import styles from './login.module.css';
import { isMockMode } from '@/lib/application-mode';
import { ApiLogin } from '@/features/api/api-login';

export function LoginFlow() {
  return isMockMode() ? <MockLoginFlow /> : <ApiLogin />;
}

function MockLoginFlow() {
  const { revision, student } = useDemo();
  if (student) {
    return (
      <div className={styles.signedIn}>
        <span className="card-kicker">PHIÊN DEMO ĐANG MỞ</span>
        <h2>Chào bạn, {student.displayName}.</h2>
        <p>Bạn có thể tiếp tục vào trang chủ để sử dụng dịch vụ tư vấn.</p>
        <Link className="primary-link" href="/home">
          Vào trang chủ <span aria-hidden="true">→</span>
        </Link>
      </div>
    );
  }
  return <LoginForm key={revision} />;
}

function LoginForm() {
  const { services, setStudent, resetDemo } = useDemo();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [validation, setValidation] = useState({ email: '', password: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const emailInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const alert = useRef<HTMLDivElement>(null);
  const configuration = services.getConfiguration();
  const scenario = DEMO_SCENARIOS.find((item) => item.id === configuration.scenario);

  useEffect(
    () => () => {
      pending.current?.abort();
      pending.current = null;
    },
    [],
  );
  useEffect(() => {
    if (error) alert.current?.focus();
  }, [error]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    const nextValidation = {
      email: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
        ? ''
        : 'Nhập email đúng định dạng, ví dụ student@example.test.',
      password: password.length ? '' : 'Nhập mật khẩu minh họa để tiếp tục.',
    };
    setValidation(nextValidation);
    setError('');
    if (nextValidation.email || nextValidation.password) {
      (nextValidation.email ? emailInput : passwordInput).current?.focus();
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    const submittedPassword = password;
    setPassword('');
    try {
      const student = await services.loginDemo(
        { email: email.trim(), password: submittedPassword },
        { signal: controller.signal },
      );
      if (controller.signal.aborted || pending.current !== controller) return;
      setStudent(student);
      router.replace('/home');
    } catch (cause: unknown) {
      if (controller.signal.aborted || pending.current !== controller || isRequestCancelled(cause))
        return;
      setError(cause instanceof Error ? cause.message : 'Chưa thể đăng nhập. Vui lòng thử lại.');
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <>
      <span className="card-kicker">01 / ĐĂNG NHẬP</span>
      <h2>Đăng nhập demo</h2>
      <p className={styles.description}>Mở không gian sinh viên và bắt đầu một cuộc hẹn tư vấn.</p>
      <p className={styles.notice}>
        Dùng email và mật khẩu minh họa. Hệ thống luôn trả về danh tính sinh viên giả; không dùng
        thông tin đăng nhập thật.
      </p>
      <form
        className={styles.form}
        onSubmit={submit}
        noValidate
        aria-label="Đăng nhập demo"
        aria-busy={busy}
      >
        {error && (
          <div className={styles.error} role="alert" tabIndex={-1} ref={alert}>
            {error}
          </div>
        )}
        <fieldset disabled={busy}>
          <div className={styles.field}>
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              ref={emailInput}
              type="email"
              autoComplete="off"
              inputMode="email"
              required
              value={email}
              placeholder="student@example.test"
              aria-invalid={Boolean(validation.email)}
              aria-describedby={validation.email ? 'email-error' : undefined}
              onChange={(event) => setEmail(event.target.value)}
            />
            {validation.email && (
              <span id="email-error" className={styles.fieldError}>
                {validation.email}
              </span>
            )}
          </div>
          <div className={styles.field}>
            <label htmlFor="login-password">Mật khẩu</label>
            <input
              id="login-password"
              ref={passwordInput}
              type="password"
              autoComplete="off"
              required
              value={password}
              placeholder="Mật khẩu minh họa"
              aria-invalid={Boolean(validation.password)}
              aria-describedby={validation.password ? 'password-error' : undefined}
              onChange={(event) => setPassword(event.target.value)}
            />
            {validation.password && (
              <span id="password-error" className={styles.fieldError}>
                {validation.password}
              </span>
            )}
          </div>
          <button className={styles.submit} type="submit">
            {busy ? 'Đang đăng nhập…' : 'Đăng nhập demo'} <span aria-hidden="true">→</span>
          </button>
        </fieldset>
        <p className={styles.sessionNote} role="status">
          {busy
            ? 'Đang mở phiên demo. Bạn có thể rời trang để hủy.'
            : 'Phiên demo chỉ được giữ trong bộ nhớ và kết thúc khi tải lại trang.'}
        </p>
      </form>
      <details className={styles.controls}>
        <summary>
          Thiết lập bản demo <span>{scenario?.label}</span>
        </summary>
        <div>
          <label htmlFor="login-scenario">Kịch bản trải nghiệm</label>
          <select
            id="login-scenario"
            value={configuration.scenario}
            disabled={busy}
            onChange={(event) => resetDemo(event.target.value as DemoScenarioId)}
          >
            <optgroup label="Đăng nhập">
              {DEMO_SCENARIOS.filter((item) =>
                ['success', 'slow', 'invalid-login', 'login-error'].includes(item.id),
              ).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </optgroup>
            <optgroup label="Luồng đặt lịch">
              {DEMO_SCENARIOS.filter(
                (item) => !['success', 'slow', 'invalid-login', 'login-error'].includes(item.id),
              ).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </optgroup>
          </select>
          <p>{scenario?.description}</p>
          <button type="button" disabled={busy} onClick={() => resetDemo()}>
            Đặt lại demo
          </button>
        </div>
      </details>
    </>
  );
}
