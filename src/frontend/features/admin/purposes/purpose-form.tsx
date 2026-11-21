'use client';

import { useRef, useState, type FormEvent } from 'react';
import { useApi } from '@/features/api/api-provider';
import type { AdminPurpose } from '@/services/admin-catalog-contracts';
import { purposeMatches } from '@/services/api/admin-catalog-mapping';
import { useAdminWorkspace } from '../admin-workspace';
import type { AdminOperation } from '../use-admin-mutation';
import { AvailabilityNotice, PurposeSummary } from '../resource-summary';
import { purposeBody, purposeDraft, purposeErrors, type PurposeDraft } from './purpose-fields';
import styles from '../admin-configuration.module.css';

export function PurposeForm({ original }: { original: AdminPurpose | null }) {
  const { session } = useApi();
  const { mutation, save, setPurposeEditor } = useAdminWorkspace();
  const [draft, setDraft] = useState(() => purposeDraft(original));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirmation, setConfirmation] = useState<Exclude<
    AdminOperation,
    { kind: 'configuration' }
  > | null>(null);
  const [message, setMessage] = useState('');
  const summary = useRef<HTMLDivElement>(null);
  const locked =
    mutation.busy ||
    mutation.state.phase === 'unknown' ||
    mutation.state.phase === 'conflict' ||
    session.status !== 'authenticated' ||
    Boolean(session.verifying);
  function change<K extends keyof PurposeDraft>(field: K, value: PurposeDraft[K]) {
    setDraft((d) => ({ ...d, [field]: value }));
    setConfirmation(null);
    setMessage('');
  }
  function review(event: FormEvent) {
    event.preventDefault();
    if (locked) return;
    const next = purposeErrors(draft);
    setErrors(next);
    if (Object.keys(next).length) {
      summary.current?.focus();
      return;
    }
    const body = purposeBody(draft);
    if (original && purposeMatches(original, body)) {
      setMessage('Không có thay đổi.');
      return;
    }
    setConfirmation(
      original
        ? {
            kind: 'purpose-replace',
            id: original.id,
            code: original.code,
            body: {
              expected_updated_at: original.updated_at,
              name: body.name,
              duration_minutes: body.duration_minutes,
              requires_free_text: body.requires_free_text,
              is_active: body.is_active,
            },
          }
        : { kind: 'purpose-create', code: body.code, body },
    );
  }
  return (
    <section className={styles.panel}>
      <h2>{original ? `Sửa nhu cầu ${original.code}` : 'Tạo nhu cầu tư vấn'}</h2>
      <AvailabilityNotice />
      <div ref={summary} tabIndex={-1} role={Object.keys(errors).length ? 'alert' : undefined}>
        {Object.entries(errors).map(([key, text]) => (
          <p key={key} className={styles.error}>
            <a href={`#purpose-${key}`}>{text}</a>
          </p>
        ))}
      </div>
      <form aria-label="Thông tin nhu cầu tư vấn" noValidate onSubmit={review}>
        <fieldset className={styles.fields} disabled={locked || Boolean(confirmation)}>
          <label>
            Mã nhu cầu
            <input
              id="purpose-code"
              value={draft.code}
              readOnly={Boolean(original)}
              aria-invalid={Boolean(errors.code)}
              onChange={(e) => change('code', e.target.value)}
            />
          </label>
          <label>
            Tên nhu cầu
            <input
              id="purpose-name"
              value={draft.name}
              aria-invalid={Boolean(errors.name)}
              onChange={(e) => change('name', e.target.value)}
            />
          </label>
          <label>
            Thời lượng (phút)
            <input
              id="purpose-duration_minutes"
              type="number"
              min="1"
              max="32767"
              step="1"
              value={draft.duration_minutes}
              aria-invalid={Boolean(errors.duration_minutes)}
              onChange={(e) => change('duration_minutes', e.target.value)}
            />
          </label>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={draft.requires_free_text}
              onChange={(e) => change('requires_free_text', e.target.checked)}
            />
            Yêu cầu nội dung tự do
          </label>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={draft.is_active}
              onChange={(e) => change('is_active', e.target.checked)}
            />
            Hoạt động
          </label>
        </fieldset>
        <div className={styles.actions}>
          <button
            className={styles.primary}
            type="submit"
            disabled={locked || Boolean(confirmation)}
          >
            Xem lại nhu cầu
          </button>
          <button type="button" disabled={locked} onClick={() => setPurposeEditor(null)}>
            Đóng biểu mẫu
          </button>
        </div>
      </form>
      {message && <p role="status">{message}</p>}
      {confirmation && (
        <section aria-label="Xác nhận nhu cầu tư vấn" tabIndex={-1} ref={(node) => node?.focus()}>
          <h3>Xem lại trước khi lưu</h3>
          <PurposeSummary value={confirmation.body} code={draft.code} />
          {!confirmation.body.is_active && (
            <p>
              Ngừng hoạt động chặn đặt lịch và nhận đề xuất mới. Mã, liên kết nhân viên và lịch sử
              được giữ nguyên.
            </p>
          )}
          <div className={styles.actions}>
            <button
              type="button"
              disabled={locked}
              onClick={() => {
                const operation = confirmation;
                setConfirmation(null);
                void save(operation);
              }}
            >
              Xác nhận lưu nhu cầu
            </button>
            <button type="button" disabled={locked} onClick={() => setConfirmation(null)}>
              Quay lại chỉnh sửa
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
