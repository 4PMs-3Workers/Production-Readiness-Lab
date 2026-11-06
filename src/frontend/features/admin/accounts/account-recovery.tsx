'use client';

import { useCallback } from 'react';
import { useApi } from '@/features/api/api-provider';
import { useApiResource } from '@/features/api/use-api-resource';
import { ApiFeedback } from '@/features/api/api-feedback';
import type { AccountFields } from '@/services/admin-contracts';
import type { AccountOperation } from './use-admin-account-mutation';
import styles from './admin-accounts.module.css';

const fields: readonly [keyof AccountFields, string][] = [
  ['email', 'Email'],
  ['name', 'Tên'],
  ['role', 'Vai trò'],
  ['is_active', 'Trạng thái'],
  ['student_code', 'Mã sinh viên'],
  ['faculty', 'Khoa'],
  ['major', 'Ngành'],
  ['gender', 'Giới tính'],
];
const display = (value: AccountFields[keyof AccountFields]) =>
  value === null
    ? 'Không có'
    : typeof value === 'boolean'
      ? value
        ? 'Hoạt động'
        : 'Không hoạt động'
      : value;

export function AccountRecovery({
  operation,
  onReviewed,
}: {
  operation: AccountOperation;
  onReviewed: () => void;
}) {
  const { services, session, revision } = useApi();
  const actor = session.status === 'authenticated' && !session.verifying ? session.user.id : '';
  const generation = session.status === 'authenticated' ? session.user.sessionGeneration : '';
  const load = useCallback(
    async (signal: AbortSignal) => {
      if (!actor) throw new DOMException('Session verification', 'AbortError');
      // The API has no GET-by-ID. Scan independent of the visible list's filters,
      // using stable identity even if another admin has changed the target email.
      const email = operation.fields.email.trim().toLowerCase();
      const search = operation.targetId ? undefined : Array.from(email).slice(0, 100).join('');
      for (let page = 1; ; page++) {
        const result = await services.listAdminAccounts(
          { page, pageSize: 100, search },
          {
            signal,
            expectedUserId: actor,
            expectedSessionGeneration: generation,
          },
        );
        const account = result.items.find((row) =>
          operation.targetId ? row.id === operation.targetId : row.email === email,
        );
        if (account) return account;
        if (page * result.pageSize >= result.total) return null;
      }
    },
    [services, actor, operation, generation],
  );
  const resource = useApiResource(`recovery:${actor}:${revision}`, load);
  return (
    <section aria-label="Đối chiếu tài khoản" className={styles.recovery}>
      <h2>Đối chiếu tài khoản</h2>
      {resource.state.status !== 'success' ? (
        <ApiFeedback
          loading={resource.state.status === 'loading'}
          message={resource.state.status === 'error' ? resource.state.message : undefined}
        />
      ) : (
        <>
          {resource.state.data ? (
            <>
              <p>ID: {resource.state.data.id}</p>
              <p>Thời điểm cập nhật hiện tại: {resource.state.data.updated_at}</p>
            </>
          ) : (
            <p>
              Không tìm thấy tài khoản{' '}
              {operation.targetId
                ? `có ID ${operation.targetId}`
                : `có email chính xác ${operation.fields.email}`}{' '}
              trong dữ liệu vừa đọc. Điều này không xác nhận kết quả yêu cầu trước.
            </p>
          )}
          <table>
            <caption>So sánh thông tin đã gửi với dữ liệu hiện tại</caption>
            <thead>
              <tr>
                <th>Trường</th>
                <th>Thông tin đã gửi</th>
                <th>Dữ liệu hiện tại</th>
              </tr>
            </thead>
            <tbody>
              {fields.map(([key, label]) => (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  <td>{display(operation.fields[key])}</td>
                  <td>
                    {resource.state.status === 'success' && resource.state.data
                      ? display(resource.state.data[key])
                      : 'Không tìm thấy'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" onClick={onReviewed}>
            Tôi đã xem dữ liệu, bắt đầu thao tác mới
          </button>
        </>
      )}
      <button
        type="button"
        onClick={resource.retry}
        disabled={!actor || resource.state.status === 'loading'}
      >
        Tải lại để xem xét
      </button>
    </section>
  );
}
