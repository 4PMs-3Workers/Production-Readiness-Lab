import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffSessionGate, safeStaffReturnPath } from '@/features/staff/staff-session-gate';
import { safeReturnPath } from '@/features/api/session-gate';

const router = vi.hoisted(() => ({ replace: vi.fn() }));
const state = vi.hoisted(() => ({
  value: {
    session: {
      status: 'loading' as string,
      user: undefined as undefined | { id: string; displayName: string; role: string },
      verifying: false,
    },
    refreshSession: vi.fn(),
    logout: vi.fn(),
    revision: 0,
  },
}));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/staff/appointments',
  useSearchParams: () => new URLSearchParams('page=2'),
}));
vi.mock('@/features/api/api-provider', () => ({ useApi: () => state.value }));

const staffUser = { id: 'staff-a', displayName: 'Nhân viên mẫu', role: 'STAFF' };

beforeEach(() => {
  router.replace.mockClear();
  state.value = {
    session: { status: 'loading', user: undefined, verifying: false },
    refreshSession: vi.fn(),
    logout: vi.fn(),
    revision: 0,
  };
});

describe('StaffSessionGate', () => {
  it('keeps private content hidden while session bootstrap is unresolved', () => {
    render(
      <StaffSessionGate>
        <p>private staff data</p>
      </StaffSessionGate>,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Đang kiểm tra phiên đăng nhập');
    expect(screen.queryByText('private staff data')).not.toBeInTheDocument();
  });

  it('renders the portal only for STAFF', () => {
    state.value.session = { status: 'authenticated', user: staffUser, verifying: false };
    render(
      <StaffSessionGate>
        <p>private staff data</p>
      </StaffSessionGate>,
    );

    expect(screen.getByText('private staff data')).toBeVisible();
  });

  it.each(['STUDENT', 'ADMIN'] as const)('does not render staff data for the %s role', (role) => {
    state.value.session = {
      status: 'authenticated',
      user: { ...staffUser, role },
      verifying: false,
    };
    render(
      <StaffSessionGate>
        <p>private staff data</p>
      </StaffSessionGate>,
    );

    expect(screen.getByRole('heading', { name: 'Tài khoản chưa được hỗ trợ' })).toBeVisible();
    expect(screen.queryByText('private staff data')).not.toBeInTheDocument();
  });

  it('redirects an anonymous deep link to login with its safe internal return path', () => {
    state.value.session = { status: 'anonymous', user: undefined, verifying: false };
    render(
      <StaffSessionGate>
        <p>private staff data</p>
      </StaffSessionGate>,
    );

    expect(router.replace).toHaveBeenCalledWith(
      '/login?returnTo=%2Fstaff%2Fappointments%3Fpage%3D2',
    );
    expect(screen.queryByText('private staff data')).not.toBeInTheDocument();
  });

  it.each(['https://evil.invalid', '//evil.invalid', '/\\evil.invalid', '/staff/admin'])(
    'rejects unsafe or unsupported staff return path %s',
    (value) => expect(safeStaffReturnPath(value)).toBe('/staff/appointments'),
  );

  it('allows only an internal staff route through the shared login return-path gate', () => {
    expect(safeReturnPath('/staff/appointments/abc?status=UPCOMING')).toBe(
      '/staff/appointments/abc?status=UPCOMING',
    );
    expect(safeReturnPath('/staff/admin')).toBe('/home');
  });
});
