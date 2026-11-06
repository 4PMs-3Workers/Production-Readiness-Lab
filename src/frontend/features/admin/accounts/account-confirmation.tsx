'use client';

import { useEffect, useRef } from 'react';
import styles from './admin-accounts.module.css';

interface ConfirmationProps {
  label: string;
  changes: string;
  self: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}
export function AccountConfirmation({
  label,
  changes,
  self,
  onConfirm,
  onCancel,
}: ConfirmationProps) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
  }, []);
  return (
    <section className={styles.notice} role="region" aria-label="Xác nhận thay đổi tài khoản">
      <h3>Xác nhận thay đổi tài khoản</h3>
      <p>{label}</p>
      <p>{changes}</p>
      <p>
        Thay đổi vai trò, trạng thái hoặc mật khẩu sẽ kết thúc các phiên đăng nhập của tài khoản
        này.
      </p>
      {self && (
        <p>
          Bạn đang thay đổi tài khoản của mình và cần đăng nhập lại. Vai trò mới có thể không có
          quyền quản trị.
        </p>
      )}
      <div className={styles.actions}>
        <button type="button" ref={cancel} onClick={onCancel}>
          Quay lại chỉnh sửa
        </button>
        <button type="button" onClick={onConfirm}>
          Xác nhận thay đổi
        </button>
      </div>
    </section>
  );
}
