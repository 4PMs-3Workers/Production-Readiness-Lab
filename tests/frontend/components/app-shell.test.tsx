import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/components/app-shell';

describe('AppShell', () => {
  it('provides demo disclosure, navigation and a skip target around the page', () => {
    render(
      <AppShell>
        <h1>Nội dung trang</h1>
      </AppShell>,
    );

    expect(screen.getByText('Bản demo dùng dữ liệu giả. Chưa tạo lịch hẹn thật.')).toBeVisible();
    const navigation = screen.getByRole('navigation', { name: 'Điều hướng chính' });
    expect(within(navigation).getByRole('link', { name: 'Đăng nhập' })).toHaveAttribute(
      'href',
      '/login',
    );
    expect(within(navigation).getByRole('link', { name: 'Đặt lịch' })).toHaveAttribute(
      'href',
      '/booking',
    );
    expect(within(navigation).getByRole('link', { name: 'Kết quả' })).toHaveAttribute(
      'href',
      '/booking/result',
    );
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content');
    expect(screen.getByRole('link', { name: 'Đến nội dung chính' })).toHaveAttribute(
      'href',
      '#main-content',
    );
  });
});
