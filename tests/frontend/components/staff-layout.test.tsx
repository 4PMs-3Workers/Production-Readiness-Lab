import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StaffPortalLayout } from '@/features/staff/staff-layout';

vi.mock('@/lib/application-mode', () => ({ isMockMode: () => false }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/staff/appointments',
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/features/api/api-provider', () => ({
  useApi: () => ({
    session: {
      status: 'authenticated',
      user: { id: 'staff-a', displayName: 'Nhân viên mẫu', role: 'STAFF' },
    },
    revision: 0,
    logout: vi.fn(),
    refreshSession: vi.fn(),
  }),
}));

describe('StaffPortalLayout', () => {
  it('provides HUS staff branding and links to the three staff workflows', () => {
    render(
      <StaffPortalLayout section="appointments">
        <h1>Lịch được giao</h1>
      </StaffPortalLayout>,
    );

    expect(
      screen.getByRole('link', { name: 'HUS — Cổng tư vấn — Trang nhân viên' }),
    ).toHaveAttribute('href', '/staff/appointments');
    expect(screen.getByRole('link', { name: 'Trang chủ' })).toHaveAttribute(
      'href',
      '/staff/appointments',
    );
    expect(screen.getByRole('link', { name: 'Không gian nhân viên' })).toHaveAttribute(
      'href',
      '/staff/appointments',
    );
    expect(screen.getByRole('navigation', { name: 'Điều hướng nhân viên' })).toBeVisible();
    expect(screen.getByRole('link', { name: /Lịch được giao/ })).toHaveAttribute(
      'href',
      '/staff/appointments',
    );
    expect(screen.getByRole('link', { name: /Lịch được giao/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('link', { name: /Nhu cầu tiếp nhận/ })).toHaveAttribute(
      'href',
      '/staff/preferences',
    );
    expect(screen.getByRole('link', { name: /Thời gian không nhận/ })).toHaveAttribute(
      'href',
      '/staff/unavailability',
    );
    expect(screen.getByRole('heading', { name: 'Lịch được giao' })).toBeVisible();
  });
});
