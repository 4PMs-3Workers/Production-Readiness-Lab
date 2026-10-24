import type { ProposalHistoryItem } from '@/services/proposal-contracts';
import { formatBusinessDate, formatBusinessTime, getBusinessDate } from '@/lib/business-time';
import styles from './proposals.module.css';

export const PROPOSAL_STATUS_LABELS: Record<ProposalHistoryItem['status'], string> = {
  PENDING: 'Đang chờ phản hồi',
  ACCEPTED: 'Đã chấp nhận',
  DECLINED: 'Đã từ chối',
  EXPIRED: 'Đã hết hạn',
  SUPERSEDED: 'Đã được thay thế',
  CANCELLED: 'Đã hủy',
};

export function ProposalTime({ instant, timezone }: { instant: string; timezone: string }) {
  return (
    <time dateTime={instant}>
      {formatBusinessDate(getBusinessDate(instant, timezone))} ·{' '}
      {formatBusinessTime(instant, timezone)}
    </time>
  );
}

export function ProposalValues({
  startsAt,
  endsAt,
  location,
  durationMinutes,
  timezone,
}: {
  startsAt: string;
  endsAt: string;
  location: string | null;
  durationMinutes: number;
  timezone: string;
}) {
  return (
    <dl className={styles.values}>
      <div>
        <dt>Thời gian</dt>
        <dd>
          <ProposalTime instant={startsAt} timezone={timezone} />
          {' – '}
          {getBusinessDate(startsAt, timezone) !== getBusinessDate(endsAt, timezone) && (
            <>{formatBusinessDate(getBusinessDate(endsAt, timezone))} · </>
          )}
          {formatBusinessTime(endsAt, timezone)}
        </dd>
      </div>
      <div>
        <dt>Thời lượng đã đặt</dt>
        <dd>{durationMinutes} phút</dd>
      </div>
      <div>
        <dt>Địa điểm</dt>
        <dd className={styles.exactText}>{location ?? 'Chưa có địa điểm'}</dd>
      </div>
    </dl>
  );
}

export function ProposalHistory({
  items,
  timezone,
  durationMinutes,
}: {
  items: readonly ProposalHistoryItem[];
  timezone: string;
  durationMinutes: number;
}) {
  if (!items.length) return <p>Chưa có đề xuất trên trang này.</p>;
  return (
    <ol className={styles.history} aria-label="Lịch sử đề xuất đổi lịch">
      {items.map((item) => (
        <li key={item.id}>
          <article>
            <div className={styles.historyHeading}>
              <strong>{PROPOSAL_STATUS_LABELS[item.status]}</strong>
              <span className={styles.identifier}>Mã đề xuất: {item.id}</span>
            </div>
            <ProposalValues
              startsAt={item.proposedStartsAt}
              endsAt={item.proposedEndsAt}
              location={item.proposedLocation}
              durationMinutes={durationMinutes}
              timezone={timezone}
            />
            <p>
              Đã gửi: <ProposalTime instant={item.createdAt} timezone={timezone} />
              {' · '}Hạn phản hồi: <ProposalTime instant={item.expiresAt} timezone={timezone} />
            </p>
            {item.resolvedAt && (
              <p>
                Đã xử lý: <ProposalTime instant={item.resolvedAt} timezone={timezone} />
              </p>
            )}
          </article>
        </li>
      ))}
    </ol>
  );
}
