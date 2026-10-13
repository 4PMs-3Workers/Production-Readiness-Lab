import type { Metadata } from 'next';

import { DemoPage } from '@/components/demo-page';

export const metadata: Metadata = { title: 'Kết quả đặt lịch' };

export default function BookingResultPage() {
  return (
    <DemoPage
      eyebrow="THÔNG TIN CUỘC HẸN"
      title="Kết quả đặt lịch"
      description="Thông tin cuộc hẹn sẽ xuất hiện tại đây sau khi bạn hoàn tất đặt lịch."
      action={{ href: '/booking', label: 'Quay lại đặt lịch' }}
    >
      <span className="card-kicker">03 / KẾT QUẢ</span>
      <h2>Bạn chưa có kết quả đặt lịch.</h2>
      <p>Hãy quay lại màn hình đặt lịch để bắt đầu. Bản xem trước chưa tạo cuộc hẹn.</p>
      <div className="status-note">Chưa có lịch hẹn nào được xác nhận.</div>
    </DemoPage>
  );
}
