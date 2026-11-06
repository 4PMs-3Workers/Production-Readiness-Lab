import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { AdminAccounts } from '@/features/admin/accounts/admin-accounts';
import { AdminSessionGate, safeAdminReturnPath } from '@/features/admin/admin-session-gate';
import { accountFixture as account, adminActor as actor, deferred } from '../admin-fixtures';

const routing = vi.hoisted(() => ({ replace: vi.fn() }));
const context = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('next/navigation', () => ({
  useRouter: () => routing,
  usePathname: () => '/admin/accounts',
}));
vi.mock('@/features/api/api-provider', () => ({ useApi: () => context.value }));
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  fetcher = vi.fn<typeof fetch>(async (path, init) => {
    if (String(path).endsWith('/auth/me')) return Response.json(actor);
    if (String(path).endsWith('/auth/csrf'))
      return Response.json({ user_id: actor.id, csrf_token: 'csrf' });
    if (init?.method)
      return Response.json({ ...account, updated_at: '2026-11-06T01:00:00.123457Z' });
    return Response.json({ items: [account], page: 1, page_size: 20, total: 1 });
  });
  context.value = {
    services: new ApiServices(fetcher),
    session: {
      status: 'authenticated',
      user: { ...actor, displayName: actor.name },
      verifying: false,
    },
    revision: 0,
    refreshSession: vi.fn(),
    logout: vi.fn(),
  };
  routing.replace.mockClear();
});
const writes = () =>
  fetcher.mock.calls.filter((c) => ['POST', 'PATCH'].includes(c[1]?.method ?? ''));
async function edit() {
  await userEvent.click(await screen.findByRole('button', { name: `Sửa ${account.email}` }));
}
describe('admin guard', () => {
  it.each(['STUDENT', 'STAFF'])('never mounts private account content for %s', (role) => {
    context.value.session = { status: 'authenticated', user: { ...actor, role } };
    render(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    expect(screen.getByRole('heading', { name: 'Không có quyền quản trị' })).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('redirects anonymous and holds loading private content', () => {
    context.value.session = { status: 'anonymous' };
    render(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    expect(routing.replace).toHaveBeenCalledWith('/login?returnTo=%2Fadmin%2Faccounts');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([
    'https://evil.invalid',
    '//evil.invalid',
    '/admin-evil',
    '/admin/unknown',
    '/admin/accounts\\evil',
    '/admin/accounts\n',
  ])('rejects unsafe return %s', (path) =>
    expect(safeAdminReturnPath(path)).toBe('/admin/accounts'),
  );
});
describe('admin account screen', () => {
  it.each([{ role: ['STUDENT'] }, { gender: ['MALE'] }])(
    'keeps malformed successful write responses unknown: %j',
    async (invalid) => {
      const original = fetcher.getMockImplementation()!;
      fetcher.mockImplementation((path, init) =>
        init?.method === 'PATCH'
          ? Promise.resolve(Response.json({ ...account, ...invalid }))
          : original(path, init),
      );
      render(<AdminAccounts />);
      await edit();
      fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
        target: { value: 'Changed' },
      });
      await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
      await screen.findByText(/Chưa xác định kết quả/);
      expect(screen.queryByText('Đã lưu tài khoản.')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Tạo tài khoản' })).toBeDisabled();
      expect(writes()).toHaveLength(1);
    },
  );
  it('ignores an old filter response after the query changes', async () => {
    const held = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      String(path).includes('role=STAFF') ? held.promise : original(path, init),
    );
    render(<AdminAccounts />);
    await screen.findByRole('button', { name: `Sửa ${account.email}` });
    fireEvent.change(screen.getByLabelText('Lọc vai trò'), { target: { value: 'STAFF' } });
    await waitFor(() =>
      expect(fetcher.mock.calls.some((c) => String(c[0]).includes('role=STAFF'))).toBe(true),
    );
    fireEvent.change(screen.getByLabelText('Lọc vai trò'), { target: { value: 'ADMIN' } });
    await screen.findByRole('button', { name: `Sửa ${account.email}` });
    await act(async () =>
      held.resolve(
        Response.json({
          items: [{ ...account, name: 'Stale private row' }],
          page: 1,
          page_size: 20,
          total: 1,
        }),
      ),
    );
    expect(screen.queryByText('Stale private row')).not.toBeInTheDocument();
  });
  it('hides private content while verifying and keeps the same-identity draft after verification', async () => {
    const view = render(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    await edit();
    fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
      target: { value: 'Draft remains' },
    });
    context.value.session = { status: 'authenticated', user: actor, verifying: true };
    view.rerender(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    expect(screen.getByLabelText('Tên', { exact: true })).not.toBeVisible();
    context.value.session = { status: 'authenticated', user: actor, verifying: false };
    view.rerender(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    expect(screen.getByLabelText('Tên', { exact: true })).toHaveValue('Draft remains');
    expect(writes()).toHaveLength(0);
  });
  it('shows missing-target recovery without leaving a stale editable form', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method === 'PATCH'
        ? Promise.resolve(Response.json({ error: { code: 'ACCOUNT_NOT_FOUND' } }, { status: 404 }))
        : original(path, init),
    );
    render(<AdminAccounts />);
    await edit();
    fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
      target: { value: 'Changed' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    await screen.findByRole('button', { name: 'Tải lại danh sách' });
    expect(screen.queryByRole('form', { name: 'Thông tin tài khoản' })).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });
  it('uses real filters, resets page, handles empty and never client-filters data', async () => {
    render(<AdminAccounts />);
    await screen.findByRole('button', { name: `Sửa ${account.email}` });
    fireEvent.change(screen.getByLabelText('Lọc vai trò'), { target: { value: 'STAFF' } });
    await waitFor(() =>
      expect(fetcher.mock.calls.some((c) => String(c[0]).includes('role=STAFF'))).toBe(true),
    );
    expect(screen.getByRole('button', { name: `Sửa ${account.email}` })).toBeVisible();
  });
  it('does not submit unchanged form and preserves the version on edit', async () => {
    render(<AdminAccounts />);
    await edit();
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    expect(screen.getByText('Không có thay đổi.')).toBeVisible();
    expect(writes()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
      target: { value: 'Updated' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    await screen.findByText('Đã lưu tài khoản.');
    expect(JSON.parse(String(writes()[0][1]?.body))).toEqual({
      name: 'Updated',
      expected_updated_at: account.updated_at,
    });
  });
  it('confirms role changes and cancelling writes nothing', async () => {
    render(<AdminAccounts />);
    await edit();
    fireEvent.change(screen.getByLabelText('Vai trò', { exact: true }), {
      target: { value: 'ADMIN' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    expect(screen.getByText(/Thông tin sinh viên sẽ được xóa/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Quay lại chỉnh sửa' })).toHaveFocus();
    await userEvent.click(screen.getByRole('button', { name: 'Quay lại chỉnh sửa' }));
    expect(writes()).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    await userEvent.dblClick(screen.getByRole('button', { name: 'Xác nhận thay đổi' }));
    await screen.findByText('Đã lưu tài khoản.');
    expect(writes()).toHaveLength(1);
  });
  it.each(['ACCOUNT_VERSION_CONFLICT', 'SERVICE_UNAVAILABLE'])(
    'requires explicit read/review after %s without replacing the write',
    async (code) => {
      const original = fetcher.getMockImplementation()!;
      fetcher.mockImplementation((path, init) =>
        init?.method === 'PATCH'
          ? Promise.resolve(
              Response.json(
                { error: { code } },
                { status: code === 'ACCOUNT_VERSION_CONFLICT' ? 409 : 503 },
              ),
            )
          : original(path, init),
      );
      render(<AdminAccounts />);
      await edit();
      fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
        target: { value: 'Changed' },
      });
      await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
      await screen.findByRole('button', { name: 'Tải lại để xem xét' });
      expect(screen.getByRole('button', { name: 'Tạo tài khoản' })).toBeDisabled();
      await userEvent.click(screen.getByRole('button', { name: 'Tải lại để xem xét' }));
      await screen.findByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' });
      expect(writes()).toHaveLength(1);
    },
  );
  it('never confirms unknown password reset from a newer version or keeps the password', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method === 'POST' ? Promise.reject(new TypeError('network')) : original(path, init),
    );
    render(<AdminAccounts />);
    await userEvent.click(
      await screen.findByRole('button', { name: `Đặt lại mật khẩu ${account.email}` }),
    );
    fireEvent.change(screen.getByLabelText('Mật khẩu mới'), { target: { value: '  exact  ' } });
    await userEvent.click(screen.getByRole('button', { name: 'Tiếp tục đặt lại mật khẩu' }));
    await userEvent.click(screen.getByRole('button', { name: 'Xác nhận thay đổi' }));
    await screen.findByText(/Mật khẩu không thể kiểm tra/);
    expect(screen.queryByText('Đã đặt lại mật khẩu.')).not.toBeInTheDocument();
    expect((screen.getByLabelText('Mật khẩu mới') as HTMLInputElement).value).toBe('');
    expect(JSON.stringify(localStorage)).not.toContain('exact');
    expect(writes()).toHaveLength(1);
  });
  it('blocks preflight on focus and retains draft for deliberate resubmit', async () => {
    const csrf = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      String(path).endsWith('/auth/csrf') ? csrf.promise : original(path, init),
    );
    render(<AdminAccounts />);
    await edit();
    fireEvent.change(screen.getByLabelText('Tên', { exact: true }), { target: { value: 'Draft' } });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await act(async () => csrf.resolve(Response.json({ user_id: actor.id, csrf_token: 'csrf' })));
    expect(writes()).toHaveLength(0);
    expect(screen.getByLabelText('Tên', { exact: true })).toHaveValue('Draft');
  });
  it('ignores an old identity response and clears forms on revision boundary', async () => {
    const held = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method === 'PATCH' ? held.promise : original(path, init),
    );
    const view = render(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    await edit();
    fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
      target: { value: 'Old draft' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    context.value.revision = 1;
    view.rerender(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    await act(async () => held.resolve(Response.json(account)));
    expect(screen.queryByText('Đã lưu tài khoản.')).not.toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Thông tin tài khoản' })).not.toBeInTheDocument();
  });
  it('does not claim success while receipt is pending', async () => {
    const held = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method === 'PATCH' ? held.promise : original(path, init),
    );
    render(<AdminAccounts />);
    await edit();
    fireEvent.change(screen.getByLabelText('Tên', { exact: true }), {
      target: { value: 'Pending' },
    });
    fireEvent.submit(screen.getByRole('form', { name: 'Thông tin tài khoản' }));
    fireEvent.submit(screen.getByRole('form', { name: 'Thông tin tài khoản' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(screen.queryByText('Đã lưu tài khoản.')).not.toBeInTheDocument();
    await act(async () => held.resolve(Response.json(account)));
    expect(await screen.findByText('Đã lưu tài khoản.')).toBeVisible();
  });
  it('shows field validation without sending credentials', async () => {
    render(<AdminAccounts />);
    await userEvent.click(screen.getByRole('button', { name: 'Tạo tài khoản' }));
    const form = screen.getByRole('form', { name: 'Thông tin tài khoản' });
    fireEvent.change(within(form).getByLabelText('Email'), { target: { value: 'bad' } });
    fireEvent.submit(form);
    expect(screen.getByText('Email chưa hợp lệ.')).toBeVisible();
    expect(writes()).toHaveLength(0);
  });
});
