'use client';

import { useEffect, useRef, useState } from 'react';
import { useApi } from '@/features/api/api-provider';
import { errorMessage } from '@/services/api/errors';
import { policyMatches } from '@/services/api/admin-configuration-mapping';
import { purposeMatches } from '@/services/api/admin-catalog-mapping';
import { useAdminWorkspace } from './admin-workspace';
import type { AdminOperation, AdminReceipt } from './use-admin-mutation';
import { ConfigurationSummary, PurposeSummary } from './resource-summary';
import styles from './admin-configuration.module.css';

export function AdminRecovery() {
  const { mutation } = useAdminWorkspace();
  const state = mutation.state;
  if (state.phase !== 'unknown' && state.phase !== 'conflict') return null;
  return (
    <Recovery
      operation={state.operation}
      unknown={state.phase === 'unknown'}
      message={state.message}
    />
  );
}
function Recovery({
  operation,
  unknown,
  message,
}: {
  operation: AdminOperation;
  unknown: boolean;
  message: string;
}) {
  const { services, session } = useApi();
  const workspace = useAdminWorkspace();
  const [review, setReview] = useState<{ current: AdminReceipt | null } | null>(null);
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
    return () => controller.current?.abort();
  }, []);
  async function check() {
    if (session.status !== 'authenticated' || session.verifying || reading) return;
    const request = new AbortController();
    controller.current?.abort();
    controller.current = request;
    setReading(true);
    setReview(null);
    setError('');
    const options = {
      expectedUserId: session.user.id,
      expectedSessionGeneration: session.user.sessionGeneration,
      signal: request.signal,
    };
    try {
      let current: AdminReceipt | null = null;
      if (operation.kind === 'configuration')
        current = await services.getAdminBookingConfiguration(options);
      else if (operation.kind === 'purpose-replace')
        current = await services.getAdminPurpose(operation.id, options);
      else {
        for (let page = 1; ; page++) {
          const result = await services.listAdminPurposes({ page, pageSize: 100 }, options);
          current = result.items.find((p) => p.code === operation.code) ?? null;
          if (current || page * result.pageSize >= result.total) break;
        }
      }
      if (!request.signal.aborted) setReview({ current });
    } catch (failure) {
      if (!request.signal.aborted) setError(errorMessage(failure));
    } finally {
      if (!request.signal.aborted) setReading(false);
    }
  }
  const current = review?.current;
  const matched =
    current &&
    (operation.kind === 'configuration'
      ? 'windows' in current && policyMatches(current, operation.body)
      : 'code' in current &&
        current.code === operation.code &&
        purposeMatches(current, operation.body));
  return (
    <section className={`${styles.page} ${styles.panel}`} aria-label="Xem xét kết quả quản trị">
      <h2 ref={heading} tabIndex={-1}>
        {unknown ? 'Chưa xác định kết quả lưu' : 'Dữ liệu đã thay đổi'}
      </h2>
      <p role="alert">{message}</p>
      <p>
        Không bắt đầu yêu cầu thay thế trước khi kiểm tra. Gửi lại giữ nguyên nội dung và phiên bản
        của yêu cầu cũ.
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          disabled={reading || workspace.mutation.busy}
          onClick={() => void check()}
        >
          Kiểm tra trạng thái hiện tại
        </button>
        {unknown && (
          <button
            type="button"
            disabled={reading || workspace.mutation.busy}
            onClick={() => void workspace.save(operation, true)}
          >
            Gửi lại đúng yêu cầu cũ
          </button>
        )}
      </div>
      {reading && <p role="status">Đang kiểm tra…</p>}
      {error && <p role="alert">{error}</p>}
      {review && (
        <>
          <div className={styles.comparison}>
            <section>
              <h3>Yêu cầu đã gửi</h3>
              {operation.kind === 'configuration' ? (
                <ConfigurationSummary value={operation.body} />
              ) : (
                <PurposeSummary value={operation.body} code={operation.code} />
              )}
            </section>
            <section>
              <h3>Trạng thái hiện tại</h3>
              {current ? (
                'windows' in current ? (
                  <ConfigurationSummary value={current} />
                ) : (
                  <PurposeSummary value={current} code={current.code} />
                )
              ) : (
                <p>
                  Chưa tìm thấy code trong toàn bộ danh mục. Yêu cầu trước vẫn có thể đang xử lý.
                </p>
              )}
            </section>
          </div>
          <p>
            {matched
              ? 'Trạng thái hiện tại khớp đầy đủ yêu cầu đã gửi.'
              : 'Trạng thái hiện tại chưa khớp. Điều này không chứng minh yêu cầu trước đã rollback; dữ liệu cũng có thể đã được sửa tiếp.'}
          </p>
          <button
            type="button"
            disabled={reading}
            onClick={() => {
              if (current) workspace.acceptReceipt(current);
              workspace.mutation.reset();
            }}
          >
            {matched ? 'Dùng trạng thái đã xác minh' : 'Tôi đã xem kết quả, bắt đầu chỉnh sửa mới'}
          </button>
        </>
      )}
    </section>
  );
}
