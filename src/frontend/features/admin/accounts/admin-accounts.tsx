'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useApi } from '@/features/api/api-provider';
import { useApiResource } from '@/features/api/use-api-resource';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
import type { AdminAccount, AccountFields, AccountRole } from '@/services/admin-contracts';
import { AccountForm } from './account-form';
import { AccountRecovery } from './account-recovery';
import { accountChanges } from './account-fields';
import { useAdminAccountMutation } from './use-admin-account-mutation';
import styles from './admin-accounts.module.css';

interface Editor {
  mode: 'create' | 'update' | 'password';
  account: AdminAccount | null;
}
export function AdminAccounts() {
  const { services, session, revision } = useApi();
  const actorId =
    session.status === 'authenticated' && session.user.role === 'ADMIN' ? session.user.id : '';
  const verifying = session.status !== 'authenticated' || Boolean(session.verifying);
  const generation = session.status === 'authenticated' ? session.user.sessionGeneration : '';
  const [query, setQuery] = useState({ page: 1, role: '', active: '', search: '' });
  const [search, setSearch] = useState('');
  const [editor, setEditor] = useState<Editor | null>(null);
  const [reviewStarted, setReviewStarted] = useState(false);
  const mutation = useAdminAccountMutation();
  const feedback = useRef<HTMLDivElement>(null);
  const createButton = useRef<HTMLButtonElement>(null);
  const blocked = mutation.state.phase === 'conflict' || mutation.state.phase === 'unknown';
  const missing = 'errorCode' in mutation.state && mutation.state.errorCode === 'ACCOUNT_NOT_FOUND';
  const load = useCallback(
    async (signal: AbortSignal) => {
      if (verifying || !actorId)
        return Promise.reject(new DOMException('Session verification', 'AbortError'));
      const page = await services.listAdminAccounts(
        {
          page: query.page,
          role: (query.role || undefined) as AccountRole | undefined,
          isActive: query.active ? query.active === 'true' : undefined,
          search: query.search,
        },
        { signal, expectedUserId: actorId, expectedSessionGeneration: generation },
      );
      const lastPage = Math.max(1, Math.ceil(page.total / page.pageSize));
      if (page.page > lastPage) {
        // A mutation may remove the final row on this page. Refetch using the
        // server total before displaying an empty result for these filters.
        setQuery((current) => (current === query ? { ...current, page: lastPage } : current));
      }
      return page;
    },
    [services, query, actorId, verifying, generation],
  );
  const resource = useApiResource(
    `admin:${actorId}:${revision}:${JSON.stringify(query)}:${verifying}`,
    load,
  );
  useEffect(() => {
    if ('message' in mutation.state) feedback.current?.focus();
  }, [mutation.state]);
  function filter(event: FormEvent) {
    event.preventDefault();
    setQuery((current) => ({ ...current, page: 1, search: search.trim() }));
  }
  function open(next: Editor) {
    if (mutation.busy || blocked || verifying) return;
    mutation.clear();
    setEditor(next);
  }
  function close() {
    setEditor(null);
    createButton.current?.focus();
  }
  async function save(fields: AccountFields, password: string) {
    if (!editor) return;
    const { account, mode } = editor;
    const revokesSelf =
      account?.id === actorId &&
      (mode === 'password' ||
        fields.role !== account.role ||
        fields.is_active !== account.is_active);
    const operation = {
      kind: mode,
      targetId: account?.id,
      label: account?.email ?? fields.email,
      fields: { ...fields },
      revokesSelf,
    };
    const result = await mutation.run(operation, (options) => {
      if (mode === 'create') return services.createAdminAccount({ ...fields, password }, options);
      if (!account) throw new Error('Account required');
      if (mode === 'password')
        return services.resetAdminAccountPassword(
          account.id,
          { password, expected_updated_at: account.updated_at },
          options,
        );
      const body = accountChanges(account, fields);
      if (!body) throw new Error('No changes');
      return services.updateAdminAccount(account.id, body, options);
    });
    if (result) {
      setEditor(null);
      resource.retry();
    }
  }
  return (
    <div className={styles.accounts}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>QUẢN TRỊ</p>
          <h1>Quản lý tài khoản</h1>
          <p>Quản lý tài khoản nội bộ và quyền truy cập.</p>
        </div>
        <button
          ref={createButton}
          type="button"
          disabled={mutation.busy || blocked || verifying}
          onClick={() => open({ mode: 'create', account: null })}
        >
          Tạo tài khoản
        </button>
      </div>
      <p className={styles.notice}>
        Phải còn ít nhất một ADMIN và một STAFF hoạt động. Không thể ngừng STAFF còn lịch giữ chỗ
        hoặc đổi vai trò của tài khoản có tham chiếu lịch sử.
      </p>
      {'message' in mutation.state && (
        <div
          ref={feedback}
          tabIndex={-1}
          role={mutation.state.phase === 'confirmed' ? 'status' : 'alert'}
          className={styles.notice}
        >
          <p>{mutation.state.message}</p>
          {missing && (
            <button
              type="button"
              onClick={() => {
                setEditor(null);
                mutation.clear();
                resource.retry();
              }}
            >
              Tải lại danh sách
            </button>
          )}
          {blocked && (
            <>
              <p>
                Thao tác:{' '}
                {mutation.state.operation.kind === 'password'
                  ? 'Đặt lại mật khẩu'
                  : mutation.state.operation.kind === 'create'
                    ? 'Tạo tài khoản'
                    : 'Sửa tài khoản'}{' '}
                — {mutation.state.operation.label}
              </p>
              <p>
                Dữ liệu đọc lại chỉ xác nhận trạng thái hiện tại, không chứng minh thao tác trước đã
                thành công.
                {mutation.state.operation.kind === 'password'
                  ? ' Mật khẩu không thể kiểm tra từ danh sách hoặc thời điểm cập nhật.'
                  : ''}
              </p>
              {!reviewStarted && (
                <button
                  type="button"
                  onClick={() => {
                    setReviewStarted(true);
                  }}
                >
                  Tải lại để xem xét
                </button>
              )}
              {reviewStarted && (
                <AccountRecovery
                  operation={mutation.state.operation}
                  onReviewed={() => {
                    mutation.clear();
                    setReviewStarted(false);
                    setEditor(null);
                    resource.retry();
                  }}
                />
              )}
            </>
          )}
        </div>
      )}
      {mutation.busy && <p role="status">Đang gửi yêu cầu…</p>}
      <form className={styles.filters} onSubmit={filter} aria-label="Lọc tài khoản">
        <label>
          Tìm theo tên hoặc email
          <input maxLength={100} value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <label>
          Lọc vai trò
          <select
            value={query.role}
            onChange={(e) => setQuery((current) => ({ ...current, page: 1, role: e.target.value }))}
          >
            <option value="">Tất cả vai trò</option>
            <option value="STUDENT">STUDENT</option>
            <option value="STAFF">STAFF</option>
            <option value="ADMIN">ADMIN</option>
          </select>
        </label>
        <label>
          Lọc trạng thái
          <select
            value={query.active}
            onChange={(e) =>
              setQuery((current) => ({ ...current, page: 1, active: e.target.value }))
            }
          >
            <option value="">Tất cả trạng thái</option>
            <option value="true">Hoạt động</option>
            <option value="false">Không hoạt động</option>
          </select>
        </label>
        <button type="submit">Tìm kiếm</button>
      </form>
      {resource.state.status !== 'success' ? (
        <ApiFeedback
          loading={resource.state.status === 'loading'}
          message={resource.state.status === 'error' ? resource.state.message : undefined}
          retry={resource.state.status === 'error' ? resource.retry : undefined}
        />
      ) : (
        <>
          {resource.state.data.items.length ? (
            <ul className={styles.list} aria-label="Danh sách tài khoản">
              {resource.state.data.items.map((account) => (
                <li key={account.id} className={styles.card}>
                  <div>
                    <h2>{account.name}</h2>
                    <p>{account.email}</p>
                    <p>
                      {account.role} · {account.is_active ? 'Hoạt động' : 'Không hoạt động'}
                    </p>
                  </div>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      disabled={mutation.busy || blocked}
                      aria-label={`Sửa ${account.email}`}
                      onClick={() => open({ mode: 'update', account })}
                    >
                      Sửa
                    </button>
                    <button
                      type="button"
                      disabled={mutation.busy || blocked}
                      aria-label={`Đặt lại mật khẩu ${account.email}`}
                      onClick={() => open({ mode: 'password', account })}
                    >
                      Đặt lại mật khẩu
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p role="status">
              {query.role || query.active || query.search
                ? 'Không có tài khoản phù hợp bộ lọc.'
                : 'Chưa có tài khoản.'}
            </p>
          )}
          <Pagination
            page={resource.state.data}
            onPage={(page) => setQuery((current) => ({ ...current, page }))}
          />
        </>
      )}
      {editor && !missing && (
        <div hidden={blocked && editor.mode !== 'update'} inert={blocked}>
          {blocked && editor.mode === 'update' && (
            <p>
              Biểu mẫu dưới đây là thay đổi chưa được xác nhận. So sánh với dữ liệu đọc lại trước
              khi bắt đầu thao tác mới.
            </p>
          )}
          <AccountForm
            key={`${editor.mode}:${editor.account?.id ?? 'new'}:${editor.account?.updated_at ?? ''}`}
            account={editor.account}
            mode={editor.mode}
            actorId={actorId}
            disabled={mutation.busy || verifying || blocked}
            onSubmit={(fields, password) => {
              void save(fields, password);
            }}
            onCancel={close}
          />
        </div>
      )}
    </div>
  );
}
