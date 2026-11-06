'use client';

import { useCallback, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import { useApiResource } from '@/features/api/use-api-resource';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
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
  const [page, setPage] = useState(1);
  const load = useCallback(
    async (signal: AbortSignal) => {
      if (!actor) throw new DOMException('Session verification', 'AbortError');
      // No GET-by-ID exists. Read independently of the main list's filters and
      // require an exact match. Additional pages require an explicit user action.
      const email = operation.fields.email.trim().toLowerCase();
      const search = operation.targetId ? undefined : Array.from(email).slice(0, 100).join('');
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
      return { account, page: result };
    },
    [services, actor, operation, generation, page],
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
          {resource.state.data.account ? (
            <>
              <p>ID: {resource.state.data.account.id}</p>
              <p>Thời điểm cập nhật hiện tại: {resource.state.data.account.updated_at}</p>
            </>
          ) : (
            <p>
              Chưa tìm thấy tài khoản{' '}
              {operation.targetId
                ? `có ID ${operation.targetId}`
                : `có email chính xác ${operation.fields.email}`}{' '}
              trên trang vừa đọc. Hãy đọc trang khác hoặc kiểm tra lại. Điều này không xác nhận tài
              khoản không tồn tại hay kết quả yêu cầu trước.
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
                    {resource.state.status === 'success' && resource.state.data.account
                      ? display(resource.state.data.account[key])
                      : 'Chưa tìm thấy'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination page={resource.state.data.page} onPage={setPage} />
          {resource.state.data.account && (
            <button type="button" onClick={onReviewed}>
              Tôi đã xem dữ liệu, bắt đầu thao tác mới
            </button>
          )}
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
