'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useApi } from '@/features/api/api-provider';
import type { BookingConfigurationReplaceBody } from '@/services/admin-configuration-contracts';
import { policyMatches } from '@/services/api/admin-configuration-mapping';
import { useAdminWorkspace } from '../admin-workspace';
import { AvailabilityNotice, ConfigurationSummary } from '../resource-summary';
import { configurationErrors, draftPolicy } from './configuration-fields';
import { WeeklyWindows } from './weekly-windows';
import styles from '../admin-configuration.module.css';

export function AdminBookingConfiguration({ section }: { section: 'settings' | 'hours' }) {
  const { session } = useApi();
  const workspace = useAdminWorkspace();
  const { configuration, load, loadConfiguration, mutation } = workspace;
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirmation, setConfirmation] = useState<BookingConfigurationReplaceBody | null>(null);
  const [discard, setDiscard] = useState(false);
  const [message, setMessage] = useState('');
  const summary = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (load === 'idle') void loadConfiguration();
  }, [load, loadConfiguration]);
  const locked =
    load === 'loading' ||
    mutation.busy ||
    mutation.state.phase === 'unknown' ||
    mutation.state.phase === 'conflict' ||
    session.status !== 'authenticated' ||
    Boolean(session.verifying);
  function review(event: FormEvent) {
    event.preventDefault();
    if (locked || !configuration) return;
    const next = configurationErrors(configuration.draft);
    setErrors(next);
    setMessage('');
    if (Object.keys(next).length) {
      summary.current?.focus();
      return;
    }
    const desired = draftPolicy(configuration.draft);
    if (policyMatches(desired, configuration.base)) {
      setMessage('Không có thay đổi.');
      return;
    }
    setConfirmation({ ...desired, expected_updated_at: configuration.base.updated_at });
  }
  const title = section === 'hours' ? 'Giờ phục vụ' : 'Quy định đặt lịch';
  if (!configuration)
    return (
      <div className={styles.page}>
        <h1>{title}</h1>
        {load === 'error' ? (
          <div role="alert">
            <p>{workspace.loadError}</p>
            <button type="button" onClick={() => void loadConfiguration()}>
              Thử tải lại cấu hình
            </button>
          </div>
        ) : (
          <p role="status">Đang tải cấu hình…</p>
        )}
      </div>
    );
  const { base, draft } = configuration;
  const dirty = !policyMatches(draftPolicy(draft), base);
  return (
    <div className={styles.page}>
      <header>
        <p className={styles.eyebrow}>QUẢN TRỊ DỊCH VỤ</p>
        <h1>{title}</h1>
      </header>
      <AvailabilityNotice />
      {load === 'loading' && <p role="status">Đang tải lại cấu hình…</p>}
      {load === 'error' && <p role="alert">{workspace.loadError}</p>}
      <p>
        Giờ phục vụ và quy định đặt lịch được lưu cùng nhau.{' '}
        {dirty ? 'Có thay đổi chưa lưu.' : 'Đang hiển thị bản đã tải từ backend.'}
      </p>
      <p>
        <Link href={section === 'hours' ? '/admin/booking-settings' : '/admin/service-hours'}>
          {section === 'hours' ? 'Chỉnh quy định đặt lịch' : 'Chỉnh giờ phục vụ'}
        </Link>{' '}
        — draft được giữ khi chuyển giữa hai trang trong phiên này.
      </p>
      {mutation.state.phase !== 'idle' &&
        !['unknown', 'conflict'].includes(mutation.state.phase) && (
          <p role={mutation.state.phase === 'rejected' ? 'alert' : 'status'}>
            {mutation.state.message}
          </p>
        )}
      {message && <p role="status">{message}</p>}
      <div ref={summary} tabIndex={-1} role={Object.keys(errors).length ? 'alert' : undefined}>
        {Object.entries(errors).map(([key, text]) => (
          <p key={key} className={styles.error}>
            <a href={`#configuration-${key}`}>{text}</a>
          </p>
        ))}
        {Object.keys(errors).length > 0 && (
          <p>Kiểm tra cả giờ phục vụ và quy định đặt lịch trước khi lưu.</p>
        )}
      </div>
      <form className={styles.panel} aria-label="Cấu hình đặt lịch" noValidate onSubmit={review}>
        {section === 'settings' ? (
          <fieldset className={styles.fields} disabled={locked || Boolean(confirmation) || discard}>
            <label>
              Múi giờ nghiệp vụ
              <input
                id="configuration-timezone"
                list="configuration-timezones"
                value={draft.timezone}
                aria-invalid={Boolean(errors.timezone)}
                onChange={(e) => {
                  workspace.setConfiguration({
                    base,
                    draft: { ...draft, timezone: e.target.value },
                  });
                  setErrors({});
                }}
              />
            </label>
            <datalist id="configuration-timezones">
              <option value="Asia/Ho_Chi_Minh" />
              <option value="UTC" />
              <option value="Etc/UTC" />
              <option value="Asia/Bangkok" />
              <option value="America/New_York" />
              <option value="Europe/London" />
            </datalist>
            <label>
              Bước bắt đầu slot (phút)
              <input
                id="configuration-start_step_minutes"
                type="number"
                min="1"
                max="32767"
                step="1"
                value={draft.start_step_minutes}
                aria-invalid={Boolean(errors.start_step_minutes)}
                onChange={(e) => {
                  workspace.setConfiguration({
                    base,
                    draft: { ...draft, start_step_minutes: e.target.value },
                  });
                  setErrors({});
                }}
              />
            </label>
            <label>
              Cửa sổ đặt lịch (ngày)
              <input
                id="configuration-booking_horizon_days"
                type="number"
                min="1"
                max="32767"
                step="1"
                value={draft.booking_horizon_days}
                aria-invalid={Boolean(errors.booking_horizon_days)}
                onChange={(e) => {
                  workspace.setConfiguration({
                    base,
                    draft: { ...draft, booking_horizon_days: e.target.value },
                  });
                  setErrors({});
                }}
              />
            </label>
            <p>
              Sinh viên có thể đặt từ ngày kế tiếp đến hết số ngày cấu hình, theo ngày nghiệp vụ của
              backend. Danh sách múi giờ chỉ là gợi ý; backend xác minh giá trị cuối cùng.
            </p>
          </fieldset>
        ) : (
          <WeeklyWindows
            draft={draft}
            errors={errors}
            disabled={locked || Boolean(confirmation) || discard}
            onChange={(next) => {
              workspace.setConfiguration({ base, draft: next });
              setErrors({});
            }}
          />
        )}
        <div className={styles.actions}>
          <button
            className={styles.primary}
            type="submit"
            disabled={locked || Boolean(confirmation) || discard}
          >
            Lưu cấu hình
          </button>
          <button
            type="button"
            disabled={locked || Boolean(confirmation) || discard}
            onClick={() => {
              if (dirty) setDiscard(true);
              else void loadConfiguration();
            }}
          >
            Tải lại cấu hình
          </button>
        </div>
      </form>
      {discard && (
        <section
          className={styles.panel}
          aria-label="Bỏ thay đổi cấu hình"
          tabIndex={-1}
          ref={(node) => node?.focus()}
        >
          <h2>Bỏ toàn bộ thay đổi chưa lưu?</h2>
          <p>Các thay đổi ở cả hai trang sẽ được thay bằng bản hiện tại từ backend.</p>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={locked}
              onClick={() => {
                setDiscard(false);
                setErrors({});
                void loadConfiguration();
              }}
            >
              Bỏ thay đổi và tải bản mới
            </button>
            <button type="button" onClick={() => setDiscard(false)}>
              Tiếp tục chỉnh sửa
            </button>
          </div>
        </section>
      )}
      {confirmation && (
        <section
          className={styles.panel}
          aria-label="Xác nhận cấu hình đặt lịch"
          tabIndex={-1}
          ref={(node) => node?.focus()}
        >
          <h2>Xem lại toàn bộ cấu hình</h2>
          <p>
            Lưu đồng thời quy định và toàn bộ giờ phục vụ sau đây. Thay đổi múi giờ giữ các giờ địa
            phương đã nhập.
          </p>
          {!confirmation.windows.length && (
            <p role="alert">Cả tuần sẽ đóng cửa, không có giờ khả dụng cho lịch mới.</p>
          )}
          <div className={styles.comparison}>
            <section>
              <h3>Bản gốc</h3>
              <ConfigurationSummary value={base} />
            </section>
            <section>
              <h3>Cấu hình sẽ lưu</h3>
              <ConfigurationSummary value={confirmation} />
            </section>
          </div>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={locked}
              onClick={() => {
                const body = confirmation;
                setConfirmation(null);
                void workspace.save({ kind: 'configuration', body });
              }}
            >
              Xác nhận lưu cấu hình
            </button>
            <button type="button" disabled={locked} onClick={() => setConfirmation(null)}>
              Quay lại chỉnh sửa
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
