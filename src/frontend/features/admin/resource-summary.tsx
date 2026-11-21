import type { PurposeValues } from '@/services/admin-catalog-contracts';
import type { BookingPolicy } from '@/services/admin-configuration-contracts';
import { canonicalWindows } from '@/services/api/admin-configuration-mapping';
import styles from './admin-configuration.module.css';

export const weekdays = [
  'Thứ Hai',
  'Thứ Ba',
  'Thứ Tư',
  'Thứ Năm',
  'Thứ Sáu',
  'Thứ Bảy',
  'Chủ nhật',
];
export function PurposeSummary({ value, code }: { value: PurposeValues; code: string }) {
  return (
    <dl className={styles.summary}>
      <dt>Mã</dt>
      <dd>{code}</dd>
      <dt>Tên</dt>
      <dd>{value.name}</dd>
      <dt>Thời lượng</dt>
      <dd>{value.duration_minutes} phút</dd>
      <dt>Nội dung tự do</dt>
      <dd>{value.requires_free_text ? 'Bắt buộc' : 'Không bắt buộc'}</dd>
      <dt>Trạng thái</dt>
      <dd>{value.is_active ? 'Hoạt động' : 'Ngừng hoạt động'}</dd>
    </dl>
  );
}
export function ConfigurationSummary({ value }: { value: BookingPolicy }) {
  const windows = canonicalWindows(value.windows);
  return (
    <dl className={styles.summary}>
      <dt>Múi giờ</dt>
      <dd>{value.timezone}</dd>
      <dt>Bước bắt đầu</dt>
      <dd>{value.start_step_minutes} phút</dd>
      <dt>Cửa sổ đặt lịch</dt>
      <dd>{value.booking_horizon_days} ngày kế tiếp</dd>
      {weekdays.map((day, index) => (
        <div key={day} style={{ display: 'contents' }}>
          <dt>{day}</dt>
          <dd>
            {windows.some((w) => w.weekday === index + 1) ? (
              <ul>
                {windows
                  .filter((w) => w.weekday === index + 1)
                  .map((w) => (
                    <li key={`${w.opens_at}:${w.closes_at}`}>
                      {w.opens_at} – {w.closes_at}
                    </li>
                  ))}
              </ul>
            ) : (
              'Đóng cửa'
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function AvailabilityNotice() {
  return (
    <p className={styles.notice}>
      Thay đổi áp dụng cho khả dụng và điều kiện đặt lịch mới. Lịch đã xác nhận giữ nguyên thời điểm
      và thời lượng đã lưu. Múi giờ mới có thể làm thay đổi cách hiển thị giờ địa phương của cùng
      thời điểm.
    </p>
  );
}
