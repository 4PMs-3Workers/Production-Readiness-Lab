'use client';

import { useEffect, useRef } from 'react';
import styles from '../booking/booking.module.css';

export function ApiFeedback({
  loading,
  message,
  retry,
}: {
  loading?: boolean;
  message?: string;
  retry?: () => void;
}) {
  const alert = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (message) alert.current?.focus();
  }, [message]);
  return (
    <div
      className={styles.feedback}
      role={message ? 'alert' : 'status'}
      ref={alert}
      tabIndex={message ? -1 : undefined}
    >
      <p>{loading ? 'Đang tải dữ liệu…' : (message ?? 'Hiện chưa có lựa chọn phù hợp.')}</p>
      {retry && (
        <button type="button" className={styles.secondaryButton} onClick={retry}>
          Thử lại
        </button>
      )}
    </div>
  );
}
