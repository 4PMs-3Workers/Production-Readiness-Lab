'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
import { useApiResource } from '@/features/api/use-api-resource';
import { errorMessage } from '@/services/api/errors';
import type { StaffPurpose } from '@/services/staff-contracts';
import styles from '@/features/booking/booking.module.css';
import staffStyles from './purpose-preferences.module.css';

export function StaffPurposePreferences() {
  const { services, session } = useApi();
  const expectedUserId =
    session.status === 'authenticated' && session.user.role === 'STAFF' ? session.user.id : null;
  const [page, setPage] = useState(1);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const activeRequest = useRef<AbortController | null>(null);
  const load = useCallback(
    (signal: AbortSignal) => services.staffPurposes(page, { signal }),
    [services, page],
  );
  const resource = useApiResource(`staff-purposes:${page}`, load);
  useEffect(() => () => activeRequest.current?.abort(), []);

  async function toggle(purpose: StaffPurpose) {
    if (pendingId) return;
    if (!expectedUserId) {
      setError('Phiên đăng nhập đã thay đổi. Hãy tải lại trang.');
      return;
    }
    const controller = new AbortController();
    activeRequest.current = controller;
    setPendingId(purpose.id);
    setError('');
    try {
      await services.setStaffPurpose(purpose.id, !purpose.isAccepting, {
        signal: controller.signal,
        expectedUserId,
      });
      if (!controller.signal.aborted) {
        resource.retry();
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(errorMessage(cause));
        resource.retry();
      }
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setPendingId(null);
    }
  }

  if (resource.state.status !== 'success')
    return (
      <ApiFeedback
        loading={resource.state.status === 'loading'}
        message={resource.state.status === 'error' ? resource.state.message : undefined}
        retry={resource.state.status === 'error' ? resource.retry : undefined}
      />
    );

  return (
    <div className={styles.booking}>
      <div className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>CẤU HÌNH TIẾP NHẬN</p>
          <h1>Nhu cầu tiếp nhận</h1>
          <p>Chọn các nhu cầu bạn có thể nhận lịch mới.</p>
        </div>
      </div>
      <p className={staffStyles.notice}>
        Ngừng nhận lịch mới không hủy hoặc thay đổi các lịch hẹn hiện có.
      </p>
      {error && (
        <p role="alert" className={staffStyles.error}>
          {error}
        </p>
      )}
      {resource.state.data.items.length === 0 ? (
        <p role="status">Chưa có nhu cầu được phân công.</p>
      ) : (
        <ul className={staffStyles.list} aria-label="Danh sách nhu cầu tư vấn">
          {resource.state.data.items.map((item) => {
            const purpose = item;
            const disabled =
              pendingId === purpose.id || (!purpose.isActive && !purpose.isAccepting);
            return (
              <li className={staffStyles.card} key={purpose.id}>
                <div>
                  <h2>{purpose.name}</h2>
                  <p>
                    {purpose.durationMinutes} phút · {purpose.code}
                  </p>
                  <p>{purpose.isActive ? 'Đang hoạt động' : 'Không hoạt động'}</p>
                </div>
                <button
                  className={staffStyles.switch}
                  type="button"
                  role="switch"
                  aria-checked={purpose.isAccepting}
                  aria-label={`${purpose.isAccepting ? 'Ngừng nhận' : 'Bật nhận'} ${purpose.name}`}
                  disabled={disabled}
                  onClick={() => void toggle(purpose)}
                >
                  {pendingId === purpose.id
                    ? 'Đang lưu…'
                    : purpose.isAccepting
                      ? 'Đang nhận lịch'
                      : 'Đang tạm dừng'}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <Pagination page={resource.state.data} onPage={setPage} />
    </div>
  );
}
