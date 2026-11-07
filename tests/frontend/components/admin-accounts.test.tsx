import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { AdminAccounts } from '@/features/admin/accounts/admin-accounts';
import { AccountRecovery } from '@/features/admin/accounts/account-recovery';
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
      return Response.json({
        user_id: actor.id,
        csrf_token: 'csrf',
        session_generation: actor.session_generation,
      });
    if (init?.method)
      return Response.json({ ...account, updated_at: '2026-11-06T01:00:00.123457Z' });
    return Response.json({ items: [account], page: 1, page_size: 20, total: 1 });
  });
  context.value = {
    services: new ApiServices(fetcher),
    session: {
      status: 'authenticated',
      user: { ...actor, displayName: actor.name, sessionGeneration: actor.session_generation },
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
  it('refetches the last valid recovery page after the search total shrinks', async () => {
    const original = fetcher.getMockImplementation()!;
    const pages: number[] = [];
    const refreshed = deferred<Response>();
    fetcher.mockImplementation((path, init) => {
      const url = new URL(String(path), 'http://test');
      if (url.pathname.endsWith('/admin/accounts')) {
        const page = Number(url.searchParams.get('page'));
        pages.push(page);
        if (pages.length === 3) return refreshed.promise;
        return Promise.resolve(
          Response.json({ items: [], page, page_size: 100, total: pages.length === 1 ? 101 : 100 }),
        );
      }
      return original(path, init);
    });
    const reviewed = vi.fn();
    render(
      <AccountRecovery
        operation={{ kind: 'create', fields: account, label: account.email }}
        onReviewed={reviewed}
      />,
    );
    await screen.findByText('Trang 1 · 101 kết quả');
    await userEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
    await waitFor(() => expect(pages).toEqual([1, 2, 1]));
    expect(
      screen.queryByRole('button', { name: 'Tôi đã xem kết quả không khớp, bắt đầu thao tác mới' }),
    ).not.toBeInTheDocument();
    expect(reviewed).not.toHaveBeenCalled();
    await act(async () =>
      refreshed.resolve(Response.json({ items: [], page: 1, page_size: 100, total: 100 })),
    );
    await screen.findByText('Trang 1 · 100 kết quả');
    await userEvent.click(
      screen.getByRole('button', { name: 'Tôi đã xem kết quả không khớp, bắt đầu thao tác mới' }),
    );
    expect(reviewed).toHaveBeenCalledOnce();
    expect(writes()).toHaveLength(0);
  });
  it('does not declare no match after an exact email was found on an earlier page', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) => {
      const url = new URL(String(path), 'http://test');
      if (url.pathname.endsWith('/admin/accounts')) {
        const page = Number(url.searchParams.get('page'));
        return Promise.resolve(
          Response.json({
            items: [{ ...account, email: page === 1 ? account.email : `${account.email}.extra` }],
            page,
            page_size: 100,
            total: 101,
          }),
        );
      }
      return original(path, init);
    });
    const reviewed = vi.fn();
    render(
      <AccountRecovery
        operation={{ kind: 'create', fields: account, label: account.email }}
        onReviewed={reviewed}
      />,
    );
    await screen.findByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' });
    await userEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
    await screen.findByText('Trang 2 · 101 kết quả');
    expect(screen.queryByText(/Đã xem hết kết quả tra cứu/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Tôi đã xem kết quả không khớp, bắt đầu thao tác mới' }),
    ).not.toBeInTheDocument();
    expect(reviewed).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Trang trước' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' }),
    );
    expect(reviewed).toHaveBeenCalledOnce();
    expect(writes()).toHaveLength(0);
  });
  it('does not clear a no-match create review until all result pages were explicitly read', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) => {
      const url = new URL(String(path), 'http://test');
      if (url.pathname.endsWith('/admin/accounts')) {
        const page = Number(url.searchParams.get('page'));
        return Promise.resolve(
          Response.json({
            items: page === 1 ? [{ ...account, email: 'new@example.invalid.extra' }] : [],
            page,
            page_size: 100,
            total: 101,
          }),
        );
      }
      return original(path, init);
    });
    const reviewed = vi.fn();
    render(
      <AccountRecovery
        operation={{
          kind: 'create',
          fields: { ...account, email: 'new@example.invalid' },
          label: 'new@example.invalid',
        }}
        onReviewed={reviewed}
      />,
    );
    await screen.findByText('Trang 1 · 101 kết quả');
    expect(
      screen.queryByRole('button', { name: 'Tôi đã xem kết quả không khớp, bắt đầu thao tác mới' }),
    ).not.toBeInTheDocument();
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).includes('page_size=100')),
    ).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
    await userEvent.click(
      await screen.findByRole('button', {
        name: 'Tôi đã xem kết quả không khớp, bắt đầu thao tác mới',
      }),
    );
    expect(reviewed).toHaveBeenCalledOnce();
    expect(writes()).toHaveLength(0);
  });
  it('reads the stable target across pages without active filters and compares every profile field', async () => {
    let recovery = false;
    const held = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) => {
      const url = new URL(String(path), 'http://test');
      if (init?.method === 'PATCH') return Promise.reject(new TypeError('lost receipt'));
      if (url.searchParams.get('page_size') === '100') {
        recovery = true;
        expect(url.searchParams.has('role')).toBe(false);
        expect(url.searchParams.has('is_active')).toBe(false);
        expect(url.searchParams.has('search')).toBe(false);
        if (url.searchParams.get('page') === '1') return held.promise;
        return Promise.resolve(
          Response.json({
            items: [
              {
                ...account,
                email: 'renamed@example.invalid',
                faculty: 'Khoa hiện tại',
                major: 'Ngành hiện tại',
                gender: 'FEMALE',
              },
            ],
            page: 2,
            page_size: 100,
            total: 101,
          }),
        );
      }
      if (url.searchParams.has('role'))
        return Promise.resolve(Response.json({ items: [], page: 1, page_size: 20, total: 0 }));
      return original(path, init);
    });
    render(<AdminAccounts />);
    await edit();
    fireEvent.change(screen.getByLabelText('Khoa'), { target: { value: 'Khoa đã gửi' } });
    fireEvent.change(screen.getByLabelText('Ngành'), { target: { value: 'Ngành đã gửi' } });
    fireEvent.change(screen.getByLabelText('Giới tính'), { target: { value: 'MALE' } });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    await screen.findByText(/Chưa xác định kết quả/);
    fireEvent.change(screen.getByLabelText('Lọc vai trò'), { target: { value: 'ADMIN' } });
    await screen.findByText('Không có tài khoản phù hợp bộ lọc.');
    expect(
      screen.queryByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Tải lại để xem xét' }));
    await waitFor(() => expect(recovery).toBe(true));
    expect(
      screen.queryByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' }),
    ).not.toBeInTheDocument();
    await act(async () =>
      held.resolve(
        Response.json({
          items: [{ ...account, id: actor.id }],
          page: 1,
          page_size: 100,
          total: 101,
        }),
      ),
    );
    const comparison = await screen.findByRole('region', { name: 'Đối chiếu tài khoản' });
    await within(comparison).findByText(/Chưa tìm thấy tài khoản có ID/);
    expect(
      within(comparison).queryByRole('button', {
        name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới',
      }),
    ).not.toBeInTheDocument();
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).includes('page_size=100')),
    ).toHaveLength(1);
    await userEvent.click(within(comparison).getByRole('button', { name: 'Trang sau' }));
    await within(comparison).findByText('renamed@example.invalid');
    for (const [field, sent, current] of [
      ['Khoa', 'Khoa đã gửi', 'Khoa hiện tại'],
      ['Ngành', 'Ngành đã gửi', 'Ngành hiện tại'],
      ['Giới tính', 'MALE', 'FEMALE'],
    ]) {
      const row = within(comparison).getByRole('row', { name: `${field} ${sent} ${current}` });
      expect(row).toBeVisible();
    }
    expect(writes()).toHaveLength(1);
    await userEvent.click(
      within(comparison).getByRole('button', { name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới' }),
    );
    expect(screen.getByRole('button', { name: 'Tạo tài khoản' })).toBeEnabled();
  });
  it.each([0, 1])(
    'allows explicit new intent after completed no-match create review (total %i)',
    async (total) => {
      let attempts = 0;
      const original = fetcher.getMockImplementation()!;
      fetcher.mockImplementation((path, init) => {
        if (init?.method === 'POST') return Promise.reject(new TypeError('lost receipt'));
        const url = new URL(String(path), 'http://test');
        if (url.searchParams.get('page_size') === '100') {
          expect(url.searchParams.get('search')).toBe('new@example.invalid');
          return ++attempts === 1
            ? Promise.reject(new TypeError('unavailable'))
            : Promise.resolve(
                Response.json({
                  items: total ? [{ ...account, email: 'new@example.invalid.extra' }] : [],
                  page: 1,
                  page_size: 100,
                  total,
                }),
              );
        }
        return original(path, init);
      });
      render(<AdminAccounts />);
      await userEvent.click(screen.getByRole('button', { name: 'Tạo tài khoản' }));
      const form = screen.getByRole('form', { name: 'Thông tin tài khoản' });
      fireEvent.change(within(form).getByLabelText('Email'), {
        target: { value: ' NEW@Example.Invalid ' },
      });
      fireEvent.change(within(form).getByLabelText('Tên'), { target: { value: 'New account' } });
      fireEvent.change(within(form).getByLabelText('Mật khẩu mới'), {
        target: { value: 'fictional secret' },
      });
      await userEvent.click(within(form).getByRole('button', { name: 'Tạo tài khoản' }));
      await screen.findByText(/Chưa xác định kết quả/);
      await userEvent.click(screen.getByRole('button', { name: 'Tải lại để xem xét' }));
      const comparison = await screen.findByRole('region', { name: 'Đối chiếu tài khoản' });
      await within(comparison).findByRole('alert');
      expect(
        within(comparison).queryByRole('button', {
          name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới',
        }),
      ).not.toBeInTheDocument();
      await userEvent.click(within(comparison).getByRole('button', { name: 'Tải lại để xem xét' }));
      await within(comparison).findByText(
        /Chưa tìm thấy tài khoản có email chính xác new@example.invalid/,
      );
      expect(
        within(comparison).queryByRole('button', {
          name: 'Tôi đã xem dữ liệu, bắt đầu thao tác mới',
        }),
      ).not.toBeInTheDocument();
      expect(within(comparison).queryByText('new@example.invalid.extra')).not.toBeInTheDocument();
      expect(within(comparison).queryByText('fictional secret')).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
      await userEvent.click(
        within(comparison).getByRole('button', {
          name: 'Tôi đã xem kết quả không khớp, bắt đầu thao tác mới',
        }),
      );
      expect(screen.getByRole('button', { name: 'Tạo tài khoản' })).toBeEnabled();
      expect(screen.queryByText('Đã tạo tài khoản.')).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
      await userEvent.click(screen.getByRole('button', { name: 'Tạo tài khoản' }));
      expect(screen.getByLabelText('Mật khẩu mới')).toHaveValue('');
    },
  );
  it.each([20, 0])('returns to a valid page after mutation leaves total %i', async (total) => {
    let saved = false;
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (path, init) => {
      if (init?.method === 'PATCH') {
        saved = true;
        return Response.json({ ...account, is_active: false });
      }
      if (String(path).includes('/admin/accounts?')) {
        const page = Number(new URL(String(path), 'http://test').searchParams.get('page'));
        return Response.json({
          items: saved && (page === 2 || total === 0) ? [] : [account],
          page,
          page_size: 20,
          total: saved ? total : 21,
        });
      }
      return original(path, init);
    });
    render(<AdminAccounts />);
    await screen.findByText('Trang 1 · 21 kết quả');
    fireEvent.change(screen.getByLabelText('Lọc trạng thái'), { target: { value: 'true' } });
    await userEvent.click(await screen.findByRole('button', { name: 'Trang sau' }));
    await screen.findByText('Trang 2 · 21 kết quả');
    await edit();
    fireEvent.change(screen.getByLabelText('Trạng thái', { exact: true }), {
      target: { value: 'false' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    await userEvent.click(screen.getByRole('button', { name: 'Xác nhận thay đổi' }));
    await screen.findByText(`Trang 1 · ${total} kết quả`);
    if (total) {
      expect(screen.getByRole('button', { name: `Sửa ${account.email}` })).toBeVisible();
      expect(screen.queryByText('Không có tài khoản phù hợp bộ lọc.')).not.toBeInTheDocument();
    } else expect(screen.getByText('Không có tài khoản phù hợp bộ lọc.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
    expect(writes()).toHaveLength(1);
  });
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
    context.value.session = {
      status: 'authenticated',
      user: { ...actor, sessionGeneration: actor.session_generation },
      verifying: true,
    };
    view.rerender(
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>,
    );
    expect(screen.getByLabelText('Tên', { exact: true })).not.toBeVisible();
    context.value.session = {
      status: 'authenticated',
      user: { ...actor, sessionGeneration: actor.session_generation },
      verifying: false,
    };
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
