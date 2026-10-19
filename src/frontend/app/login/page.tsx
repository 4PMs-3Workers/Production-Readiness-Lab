import type { Metadata } from 'next';

import { DemoPage } from '@/components/demo-page';
import { LoginFlow } from '@/features/login/login-flow';
import { isMockMode } from '@/lib/application-mode';

export const metadata: Metadata = { title: 'Đăng nhập' };

export default function LoginPage() {
  return (
    <DemoPage
      eyebrow="CHÀO MỪNG BẠN"
      title="Thêm một điểm tựa trên hành trình đại học."
      description="Tìm sự hỗ trợ cho việc học, định hướng nghề nghiệp và những lựa chọn tiếp theo."
      action={isMockMode() ? { href: '/booking', label: 'Khám phá luồng đặt lịch' } : undefined}
    >
      <LoginFlow />
    </DemoPage>
  );
}
