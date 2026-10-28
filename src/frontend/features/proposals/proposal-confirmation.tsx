'use client';

import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import booking from '@/features/booking/booking.module.css';
import styles from './proposals.module.css';

export function ProposalConfirmation({
  title,
  confirmLabel,
  busy,
  children,
  onConfirm,
  onClose,
}: {
  title: string;
  confirmLabel: string;
  busy: boolean;
  children: ReactNode;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    confirm.current?.focus();
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  useEffect(() => {
    if (busy) dialog.current?.focus();
    else confirm.current?.focus();
  }, [busy]);
  function handleKey(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (!busy) onClose();
    }
    if (event.key !== 'Tab') return;
    const controls = dialog.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
    if (!controls?.length) {
      event.preventDefault();
      dialog.current?.focus();
      return;
    }
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!Array.from(controls).some((control) => control === document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  return (
    <div className={styles.overlay}>
      <section
        className={styles.dialog}
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="proposal-confirmation-title"
        tabIndex={-1}
        onKeyDown={handleKey}
      >
        <h2 id="proposal-confirmation-title">{title}</h2>
        {children}
        <div className={booking.pagination}>
          <button
            ref={confirm}
            className={booking.primaryButton}
            type="button"
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? 'Đang gửi…' : confirmLabel}
          </button>
          <button
            className={booking.secondaryButton}
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Quay lại
          </button>
        </div>
      </section>
    </div>
  );
}
