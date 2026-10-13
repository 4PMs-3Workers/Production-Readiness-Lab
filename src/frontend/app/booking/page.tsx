import type { Metadata } from 'next';

import { DemoPage } from '@/components/demo-page';

export const metadata: Metadata = { title: 'Đặt lịch tư vấn' };

export default function BookingPage() {
  return (
    <DemoPage
      eyebrow="MỘT KHOẢNG THỜI GIAN DÀNH CHO BẠN"
      title="Đặt lịch tư vấn"
      description="Chọn nhu cầu, người tư vấn và thời gian phù hợp với lịch của bạn."
      action={{ href: '/login', label: 'Quay lại đăng nhập' }}
    >
      <span className="card-kicker">02 / ĐẶT LỊCH</span>
      <h2>Các bước đặt lịch</h2>
      <ol className="booking-steps">
        <li>Chọn nhu cầu tư vấn</li>
        <li>Chọn nhân viên tư vấn</li>
        <li>Chọn ngày và giờ</li>
        <li>Xem lại và xác nhận</li>
      </ol>
      <div className="status-note">Luồng đặt lịch đang được chuẩn bị cho bản demo.</div>
    </DemoPage>
  );
}
