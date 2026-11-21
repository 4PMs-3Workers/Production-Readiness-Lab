'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import { useApiResource } from '@/features/api/use-api-resource';
import { errorMessage } from '@/services/api/errors';
import { useAdminWorkspace } from '../admin-workspace';
import { PurposeForm } from './purpose-form';
import { AvailabilityNotice } from '../resource-summary';
import styles from '../admin-configuration.module.css';

export function AdminPurposes() {
  const { services, session } = useApi();
  const workspace = useAdminWorkspace();
  const [filter, setFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [editorError, setEditorError] = useState('');
  const [opening, setOpening] = useState(false);
  const editorRequest = useRef<AbortController | null>(null);
  useEffect(() => () => editorRequest.current?.abort(), []);
  const userId = session.status === 'authenticated' ? session.user.id : '';
  const sessionGeneration =
    session.status === 'authenticated' ? session.user.sessionGeneration : '';
  const load = useCallback(
    async (signal: AbortSignal) => {
      const result = await services.listAdminPurposes(
        { page, isActive: filter === 'all' ? undefined : filter === 'active' },
        { expectedUserId: userId, expectedSessionGeneration: sessionGeneration, signal },
      );
      const last = Math.max(1, Math.ceil(result.total / result.pageSize));
      if (!signal.aborted && page > last) setPage(last);
      return result;
    },
    [services, page, filter, userId, sessionGeneration],
  );
  const { state, retry } = useApiResource(`purposes:${workspace.catalogRevision}`, load);
  const locked =
    workspace.mutation.busy ||
    workspace.mutation.state.phase === 'unknown' ||
    workspace.mutation.state.phase === 'conflict' ||
    session.status !== 'authenticated' ||
    Boolean(session.verifying);
  async function open(id: string) {
    if (locked || opening) return;
    const controller = new AbortController();
    editorRequest.current?.abort();
    editorRequest.current = controller;
    setOpening(true);
    setEditorError('');
    try {
      const item = await services.getAdminPurpose(id, {
        expectedUserId: userId,
        expectedSessionGeneration: sessionGeneration,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        workspace.mutation.reset();
        workspace.setPurposeEditor(item);
      }
    } catch (error) {
      if (!controller.signal.aborted) setEditorError(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setOpening(false);
    }
  }
  const editor = workspace.purposeEditor;
  return (
    <div className={styles.page}>
      <header className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>QUẢN TRỊ DỊCH VỤ</p>
          <h1>Danh mục tư vấn</h1>
        </div>
        <button
          type="button"
          disabled={locked || opening}
          onClick={() => {
            workspace.mutation.reset();
            workspace.setPurposeEditor('create');
          }}
        >
          Tạo nhu cầu
        </button>
      </header>
      <AvailabilityNotice />
      {workspace.mutation.state.phase !== 'idle' &&
        !['unknown', 'conflict'].includes(workspace.mutation.state.phase) && (
          <p role={workspace.mutation.state.phase === 'rejected' ? 'alert' : 'status'}>
            {workspace.mutation.state.message}
          </p>
        )}
      {editorError && <p role="alert">{editorError}</p>}
      {editor && (
        <PurposeForm
          key={editor === 'create' ? 'create' : `${editor.id}:${editor.updated_at}`}
          original={editor === 'create' ? null : editor}
        />
      )}
      <div className={styles.actions}>
        <label>
          Lọc trạng thái nhu cầu
          <select
            value={filter}
            disabled={locked}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(1);
            }}
          >
            <option value="all">Tất cả</option>
            <option value="active">Hoạt động</option>
            <option value="inactive">Ngừng hoạt động</option>
          </select>
        </label>
        <button type="button" disabled={locked} onClick={retry}>
          Tải lại danh mục
        </button>
      </div>
      {state.status === 'loading' ? (
        <p role="status">Đang tải danh mục…</p>
      ) : state.status === 'error' ? (
        <p role="alert">{state.message}</p>
      ) : (
        <>
          {!state.data.items.length && <p>Chưa có nhu cầu trong bộ lọc này.</p>}
          <ul className={styles.list}>
            {state.data.items.map((p) => (
              <li className={styles.panel} key={p.id}>
                <div className={styles.row}>
                  <div>
                    <h2>{p.name}</h2>
                    <p>
                      {p.code} · {p.duration_minutes} phút ·{' '}
                      {p.is_active ? 'Hoạt động' : 'Ngừng hoạt động'}
                    </p>
                    <p>
                      {p.requires_free_text
                        ? 'Yêu cầu nội dung tự do'
                        : 'Không bắt buộc nội dung tự do'}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={locked || opening}
                    onClick={() => void open(p.id)}
                  >
                    Sửa {p.code}
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <nav className={styles.actions} aria-label="Phân trang nhu cầu">
            <button
              type="button"
              disabled={locked || page === 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Trang trước
            </button>
            <p>
              Trang {page} · {state.data.total} nhu cầu
            </p>
            <button
              type="button"
              disabled={locked || page * state.data.pageSize >= state.data.total}
              onClick={() => setPage((p) => p + 1)}
            >
              Trang sau
            </button>
          </nav>
        </>
      )}
    </div>
  );
}
