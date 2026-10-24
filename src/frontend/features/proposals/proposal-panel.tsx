'use client';

import { useRef, useState, useSyncExternalStore } from 'react';
import { useApi } from '@/features/api/api-provider';
import { ApiFeedback } from '@/features/api/api-feedback';
import { Pagination } from '@/features/api/api-booking-fields';
import { errorMessage } from '@/services/api/errors';
import type {
  ProposalContext,
  ProposalCreateBody,
  ProposalHistoryItem,
} from '@/services/proposal-contracts';
import { formatTimeZoneLabel } from '@/lib/business-time';
import { ProposalForm, type ProposalAppointment } from './proposal-form';
import { ProposalHistory, ProposalTime, ProposalValues } from './proposal-history';
import { ProposalConfirmation } from './proposal-confirmation';
import { useProposals } from './use-proposals';
import booking from '@/features/booking/booking.module.css';
import styles from './proposals.module.css';

interface Props {
  appointment: ProposalAppointment;
  role: 'STUDENT' | 'STAFF';
  timezone: string;
  onRefresh: (signal: AbortSignal) => Promise<unknown>;
}

export function ProposalPanel(props: Props) {
  const { session, revision } = useApi();
  if (session.status !== 'authenticated' || session.user.role !== props.role) return null;
  return (
    <ProposalPanelContent
      key={`${session.user.id}:${props.role}:${props.appointment.id}:${revision}`}
      {...props}
      userId={session.user.id}
    />
  );
}

function ProposalPanelContent({
  appointment,
  role,
  timezone,
  onRefresh,
  userId,
}: Props & { userId: string }) {
  const { proposalIntents, staffChanges } = useApi();
  const resource = useProposals({ appointment, role, userId, onRefresh });
  const otherChange = useSyncExternalStore(
    staffChanges.subscribe,
    () => (role === 'STAFF' ? staffChanges.get(userId, appointment.id) : null),
    () => null,
  );
  const [form, setForm] = useState<{
    appointment: ProposalAppointment;
    context: ProposalContext;
  } | null>(null);
  const [decision, setDecision] = useState<{
    action: 'accept' | 'decline';
    proposalId: string;
    proposal: ProposalHistoryItem;
  } | null>(null);
  const [reviewStorage, setReviewStorage] = useState(false);
  const newTrigger = useRef<HTMLButtonElement>(null);
  const statusFocus = useRef<HTMLParagraphElement>(null);
  const read = resource.state.status === 'success' ? resource.state.data : null;
  const pending = read?.pending ?? null;
  const locked =
    resource.busy ||
    resource.saved !== null ||
    Boolean(resource.storageIssue) ||
    Boolean(otherChange);

  async function create(body: ProposalCreateBody) {
    try {
      const saved = proposalIntents.prepareCreate(userId, appointment.id, body);
      const done = await resource.run(saved);
      const phase = proposalIntents.get(userId, role, appointment.id)?.phase;
      if (done || phase === 'unknown' || phase === 'confirmed' || phase === 'conflict') {
        setForm(null);
        statusFocus.current?.focus();
      }
    } catch (cause) {
      resource.setError(cause instanceof Error ? cause.message : 'Không thể chuẩn bị đề xuất.');
    }
  }

  async function decide() {
    if (!decision) return;
    try {
      const saved = proposalIntents.prepareDecision(
        userId,
        appointment.id,
        decision.proposalId,
        decision.action,
      );
      const done = await resource.run(saved);
      const phase = proposalIntents.get(userId, role, appointment.id)?.phase;
      if (done || phase === 'unknown' || phase === 'confirmed' || phase === 'conflict') {
        setDecision(null);
        statusFocus.current?.focus();
      }
    } catch (cause) {
      resource.setError(cause instanceof Error ? cause.message : 'Không thể chuẩn bị phản hồi.');
    }
  }

  return (
    <section className={styles.panel} aria-labelledby="change-proposals-title">
      <div className={styles.heading}>
        <h2 id="change-proposals-title">Đề xuất đổi lịch</h2>
        <button
          className={booking.secondaryButton}
          type="button"
          disabled={resource.busy || resource.refreshing}
          onClick={() => void resource.refresh()}
        >
          Làm mới đề xuất
        </button>
      </div>
      <p>
        Thời gian theo {formatTimeZoneLabel(timezone)}. Dữ liệu được cập nhật định kỳ từ hệ thống.
      </p>
      <p ref={statusFocus} tabIndex={-1} role={resource.error ? 'alert' : 'status'}>
        {resource.error || resource.message}
      </p>

      {resource.saved && (
        <div className={styles.notice} aria-label="Phục hồi thao tác đổi lịch">
          {resource.saved.phase === 'confirmed' ? (
            <p>
              Thao tác đã được ghi nhận. Cần tải lại lịch và đề xuất để xem trạng thái hiện tại.
            </p>
          ) : resource.saved.phase === 'conflict' ? (
            <p>
              Mã thao tác đã gắn với nội dung khác. Hãy đối chiếu lịch sử và liên hệ hỗ trợ trước
              khi gửi thêm đề xuất.
            </p>
          ) : (
            <>
              <p>
                Chưa xác định đầy đủ kết quả thao tác đã gửi. Thử lại sẽ dùng đúng nội dung và mã
                thao tác cũ; không tạo ý định mới hoặc chuyển sang đề xuất khác.
              </p>
              {resource.saved.operation.kind === 'create' ? (
                <ProposalValues
                  startsAt={resource.saved.operation.body.proposed_starts_at}
                  endsAt={new Date(
                    Date.parse(resource.saved.operation.body.proposed_starts_at) +
                      appointment.durationMinutes * 60_000,
                  ).toISOString()}
                  location={resource.saved.operation.body.proposed_location}
                  durationMinutes={appointment.durationMinutes}
                  timezone={timezone}
                />
              ) : (
                <p>
                  Phản hồi: {resource.saved.operation.action === 'accept' ? 'chấp nhận' : 'từ chối'}{' '}
                  · Mã đề xuất: {resource.saved.operation.proposalId}
                </p>
              )}
              <button
                className={booking.primaryButton}
                type="button"
                disabled={resource.busy || resource.saved.phase === 'submitting'}
                onClick={() => {
                  setForm(null);
                  setDecision(null);
                  void resource.run(resource.saved!);
                }}
              >
                {resource.busy ? 'Đang gửi…' : 'Thử lại thao tác đã gửi'}
              </button>
            </>
          )}
        </div>
      )}

      {resource.storageIssue && (
        <div className={styles.notice}>
          <p role="alert">{resource.storageIssue}</p>
          <p>
            Hãy xem lại trạng thái từ hệ thống. Nếu dữ liệu phục hồi đã hỏng, cần đối chiếu lịch sử
            trước khi bắt đầu ý định mới.
          </p>
          <button
            className={booking.secondaryButton}
            type="button"
            disabled={!read || resource.refreshing}
            onClick={() => setReviewStorage(true)}
          >
            Xem cách khôi phục bộ nhớ
          </button>
        </div>
      )}

      {resource.state.status === 'loading' ? (
        <ApiFeedback loading />
      ) : resource.state.status === 'error' ? (
        <ApiFeedback
          message={errorMessage(resource.state.error)}
          retry={() => void resource.refresh()}
        />
      ) : (
        read && (
          <>
            {pending ? (
              <>
                <div className={styles.comparison}>
                  <div>
                    <h3>Lịch gốc đang giữ chỗ</h3>
                    <ProposalValues {...appointment} timezone={timezone} />
                  </div>
                  <div>
                    <h3>Đề xuất đang chờ phản hồi</h3>
                    <ProposalValues
                      startsAt={pending.proposedStartsAt}
                      endsAt={pending.proposedEndsAt}
                      location={pending.proposedLocation}
                      durationMinutes={appointment.durationMinutes}
                      timezone={timezone}
                    />
                    <p>
                      Hạn phản hồi: <ProposalTime instant={pending.expiresAt} timezone={timezone} />
                    </p>
                    <p className={styles.identifier}>Mã đề xuất: {pending.id}</p>
                  </div>
                </div>
                <p className={styles.notice}>
                  Lịch gốc vẫn giữ chỗ trong khi chờ phản hồi. Chấp nhận mới áp dụng thay đổi; từ
                  chối sẽ hủy cuộc hẹn. Khi hết hạn, hệ thống xử lý đề xuất và cập nhật lịch.
                </p>
                {role === 'STUDENT' && (
                  <div className={booking.pagination}>
                    <button
                      className={booking.primaryButton}
                      type="button"
                      disabled={locked || resource.refreshing}
                      onClick={() => {
                        resource.setError('');
                        setDecision({
                          action: 'accept',
                          proposalId: pending.id,
                          proposal: pending,
                        });
                      }}
                    >
                      Chấp nhận đề xuất
                    </button>
                    <button
                      className={booking.secondaryButton}
                      type="button"
                      disabled={locked || resource.refreshing}
                      onClick={() => {
                        resource.setError('');
                        setDecision({
                          action: 'decline',
                          proposalId: pending.id,
                          proposal: pending,
                        });
                      }}
                    >
                      Từ chối đề xuất
                    </button>
                  </div>
                )}
              </>
            ) : (
              <p>Hiện không có đề xuất đang chờ phản hồi.</p>
            )}

            {role === 'STAFF' &&
              ['UPCOMING', 'CHANGE_PENDING'].includes(read.history.context.appointmentStatus) &&
              !form && (
                <button
                  ref={newTrigger}
                  className={booking.primaryButton}
                  type="button"
                  disabled={locked || resource.refreshing}
                  onClick={() => {
                    resource.setError('');
                    setForm({
                      appointment: { ...appointment },
                      context: { ...read.history.context },
                    });
                  }}
                >
                  {pending ? 'Thay thế đề xuất' : 'Đề xuất đổi lịch'}
                </button>
              )}
            {form && (
              <ProposalForm
                {...form}
                timezone={timezone}
                busy={resource.busy || Boolean(otherChange)}
                operationError={resource.error}
                onSubmit={(body) => void create(body)}
                onClose={() => {
                  setForm(null);
                  window.setTimeout(() => newTrigger.current?.focus(), 0);
                }}
              />
            )}
            <h3 className={booking.cancelSection}>Lịch sử đề xuất</h3>
            <ProposalHistory
              items={read.history.items}
              timezone={timezone}
              durationMinutes={appointment.durationMinutes}
            />
            <fieldset className={styles.paginationScope} disabled={resource.busy}>
              <legend className={booking.srOnly}>Phân trang lịch sử đề xuất</legend>
              <Pagination page={read.history} onPage={resource.setPage} />
            </fieldset>
          </>
        )
      )}

      {decision && (
        <ProposalConfirmation
          title={
            decision.action === 'accept'
              ? 'Chấp nhận đề xuất đổi lịch?'
              : 'Từ chối đề xuất và hủy cuộc hẹn?'
          }
          confirmLabel={decision.action === 'accept' ? 'Xác nhận chấp nhận' : 'Xác nhận từ chối'}
          busy={resource.busy}
          onClose={() => setDecision(null)}
          onConfirm={() => void decide()}
        >
          {resource.error && <p role="alert">{resource.error}</p>}
          <p>Mã đề xuất: {decision.proposalId}</p>
          <ProposalValues
            startsAt={decision.proposal.proposedStartsAt}
            endsAt={decision.proposal.proposedEndsAt}
            location={decision.proposal.proposedLocation}
            durationMinutes={appointment.durationMinutes}
            timezone={timezone}
          />
          <p>
            Hạn phản hồi: <ProposalTime instant={decision.proposal.expiresAt} timezone={timezone} />
          </p>
          <p>
            {decision.action === 'accept'
              ? 'Hệ thống kiểm tra lại thời gian và khả dụng trước khi áp dụng thay đổi. Nếu không thể chấp nhận, hãy xem trạng thái mới nhất.'
              : 'Từ chối đề xuất sẽ hủy toàn bộ cuộc hẹn và giải phóng lịch gốc. Bạn cần đặt lại nếu muốn tư vấn sau.'}
          </p>
        </ProposalConfirmation>
      )}
      {reviewStorage && (
        <ProposalConfirmation
          title="Đối chiếu dữ liệu phục hồi"
          confirmLabel="Tôi đã đối chiếu lịch sử"
          busy={resource.busy}
          onClose={() => setReviewStorage(false)}
          onConfirm={() => {
            try {
              proposalIntents.discardInvalid(userId, role, appointment.id);
              setReviewStorage(false);
              resource.setError(
                'Hãy tải lại trạng thái và xác nhận ý định mới nếu cần. Không tự gửi lại thao tác đã mất mã.',
              );
              void resource.refresh();
            } catch (cause) {
              resource.setError(
                cause instanceof Error ? cause.message : 'Không thể khôi phục bộ nhớ.',
              );
            }
          }}
        >
          <p>
            Dữ liệu phục hồi không đọc được. Lịch sử có thể chưa đủ để xác định thao tác đã commit;
            hãy đối chiếu lịch hiện tại và liên hệ hỗ trợ nếu chưa rõ. Xác nhận này chỉ xóa dữ liệu
            phục hồi bị hỏng, không gửi yêu cầu đổi lịch.
          </p>
        </ProposalConfirmation>
      )}
    </section>
  );
}
