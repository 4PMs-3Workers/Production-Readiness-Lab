import type { Metadata } from 'next';

import { DemoPage } from '@/components/demo-page';

export const metadata: Metadata = { title: 'Đăng nhập' };

export default function LoginPage() {
  return (
    <DemoPage
      eyebrow="CHÀO MỪNG BẠN"
      title="Kết nối với người có thể giúp bạn."
      description="Tìm sự hỗ trợ cho việc học, định hướng nghề nghiệp và những lựa chọn tiếp theo."
      action={{ href: '/booking', label: 'Khám phá luồng đặt lịch' }}
    >
      <span className="card-kicker">01 / ĐĂNG NHẬP</span>
      <h2>Đăng nhập demo</h2>
      <p>Màn hình đăng nhập đang được chuẩn bị. Bạn có thể xem trước khung đặt lịch tư vấn.</p>
      <div className="status-note">Bản xem trước chưa yêu cầu email hoặc mật khẩu.</div>
    </DemoPage>
  );
}
