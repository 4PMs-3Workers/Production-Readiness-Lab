'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { AccountFields, AdminAccount, AccountGender } from '@/services/admin-contracts';
import {
  EMPTY_ACCOUNT,
  normalizedFields,
  accountChanges,
  accountValidation,
} from './account-fields';
import { AccountConfirmation } from './account-confirmation';
import styles from './admin-accounts.module.css';

interface AccountFormProps {
  account: AdminAccount | null;
  mode: 'create' | 'update' | 'password';
  actorId: string;
  disabled: boolean;
  onSubmit: (fields: AccountFields, password: string) => void;
  onCancel: () => void;
}
export function AccountForm({
  account,
  mode,
  actorId,
  disabled,
  onSubmit,
  onCancel,
}: AccountFormProps) {
  const [fields, setFields] = useState<AccountFields>(account ?? EMPTY_ACCOUNT);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [message, setMessage] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const submitButton = useRef<HTMLButtonElement>(null);
  const feedback = useRef<HTMLParagraphElement>(null);
  const wasConfirming = useRef(false);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  useEffect(() => {
    if (message) feedback.current?.focus();
  }, [message]);
  useEffect(() => {
    if (wasConfirming.current && !confirm && !disabled) submitButton.current?.focus();
    wasConfirming.current = confirm;
  }, [confirm, disabled]);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    const validation =
      mode === 'password'
        ? !password || Array.from(password).length > 1024
          ? 'Mật khẩu cần có từ 1 đến 1024 ký tự.'
          : null
        : accountValidation(fields, mode === 'create' ? password : undefined);
    if (validation) {
      setMessage(validation);
      return;
    }
    if (mode === 'update' && account && !accountChanges(account, fields)) {
      setMessage('Không có thay đổi.');
      return;
    }
    setMessage('');
    if (
      mode === 'password' ||
      (account && (fields.role !== account.role || fields.is_active !== account.is_active))
    )
      setConfirm(true);
    else dispatch();
  }
  function dispatch() {
    const submitted = password;
    setPassword('');
    setConfirm(false);
    onSubmit(normalizedFields(fields), submitted);
  }
  function change<K extends keyof AccountFields>(key: K, value: AccountFields[K]) {
    setFields((current) => ({ ...current, [key]: value }));
  }
  return (
    <section className={styles.panel} aria-label="Chỉnh sửa tài khoản">
      <h2 ref={heading} tabIndex={-1}>
        {mode === 'create'
          ? 'Tạo tài khoản'
          : mode === 'password'
            ? 'Đặt lại mật khẩu'
            : 'Sửa tài khoản'}
      </h2>
      {account && <p>{account.email}</p>}
      {message && (
        <p role="status" ref={feedback} tabIndex={-1}>
          {message}
        </p>
      )}
      <form
        onSubmit={submit}
        aria-label={mode === 'password' ? 'Đặt lại mật khẩu' : 'Thông tin tài khoản'}
        aria-busy={disabled}
      >
        <fieldset disabled={disabled || confirm} className={styles.fields}>
          {mode !== 'password' && (
            <>
              <label>
                Email
                <input
                  name="account-email"
                  autoComplete="off"
                  required
                  value={fields.email}
                  onChange={(e) => change('email', e.target.value)}
                />
              </label>
              <label>
                Tên
                <input
                  name="account-name"
                  required
                  value={fields.name}
                  onChange={(e) => change('name', e.target.value)}
                />
              </label>
              <label>
                Vai trò
                <select
                  aria-label="Vai trò"
                  value={fields.role}
                  onChange={(e) => change('role', e.target.value as AccountFields['role'])}
                >
                  <option value="STUDENT">STUDENT</option>
                  <option value="STAFF">STAFF</option>
                  <option value="ADMIN">ADMIN</option>
                </select>
              </label>
              <label>
                Trạng thái
                <select
                  aria-label="Trạng thái"
                  value={String(fields.is_active)}
                  onChange={(e) => change('is_active', e.target.value === 'true')}
                >
                  <option value="true">Hoạt động</option>
                  <option value="false">Không hoạt động</option>
                </select>
              </label>
              {fields.role === 'STUDENT' && (
                <>
                  <label>
                    Mã sinh viên
                    <input
                      value={fields.student_code ?? ''}
                      onChange={(e) => change('student_code', e.target.value)}
                    />
                  </label>
                  <label>
                    Khoa
                    <input
                      value={fields.faculty ?? ''}
                      onChange={(e) => change('faculty', e.target.value)}
                    />
                  </label>
                  <label>
                    Ngành
                    <input
                      value={fields.major ?? ''}
                      onChange={(e) => change('major', e.target.value)}
                    />
                  </label>
                  <label>
                    Giới tính
                    <select
                      aria-label="Giới tính"
                      value={fields.gender ?? ''}
                      onChange={(e) =>
                        change('gender', (e.target.value || null) as AccountGender | null)
                      }
                    >
                      <option value="">Không khai báo</option>
                      <option value="MALE">Nam</option>
                      <option value="FEMALE">Nữ</option>
                      <option value="OTHER">Khác</option>
                      <option value="UNDISCLOSED">Không cung cấp</option>
                    </select>
                  </label>
                </>
              )}
            </>
          )}
          {mode !== 'update' && (
            <label>
              Mật khẩu mới
              <input
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          )}
          <div className={styles.actions}>
            <button type="submit" ref={submitButton}>
              {mode === 'create'
                ? 'Tạo tài khoản'
                : mode === 'password'
                  ? 'Tiếp tục đặt lại mật khẩu'
                  : 'Lưu thay đổi'}
            </button>
          </div>
        </fieldset>
      </form>
      {confirm && !disabled && (
        <AccountConfirmation
          label={account?.email ?? fields.email}
          changes={
            mode === 'password'
              ? 'Đặt mật khẩu mới.'
              : `Vai trò: ${account?.role} → ${fields.role}. Trạng thái: ${account?.is_active ? 'Hoạt động' : 'Không hoạt động'} → ${fields.is_active ? 'Hoạt động' : 'Không hoạt động'}.${account?.role === 'STUDENT' && fields.role !== 'STUDENT' ? ' Thông tin sinh viên sẽ được xóa.' : ''}`
          }
          self={account?.id === actorId}
          onConfirm={dispatch}
          onCancel={() => {
            setConfirm(false);
            submitButton.current?.focus();
          }}
        />
      )}
      <button type="button" disabled={disabled} onClick={onCancel}>
        Đóng biểu mẫu
      </button>
    </section>
  );
}
