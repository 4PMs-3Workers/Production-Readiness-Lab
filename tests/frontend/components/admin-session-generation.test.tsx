import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { ApiProvider } from '@/features/api/api-provider';
import { AdminSessionGate } from '@/features/admin/admin-session-gate';
import { AdminAccounts } from '@/features/admin/accounts/admin-accounts';
import { ApiServices } from '@/services/api/client';
import { accountFixture as account, adminActor as actor, deferred } from '../admin-fixtures';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => '/admin/accounts',
}));

function mount() {
  let generation = actor.session_generation;
  const fetcher = vi.fn<typeof fetch>(async (path, init) => {
    if (String(path).endsWith('/auth/me'))
      return Response.json({ ...actor, session_generation: generation });
    if (String(path).endsWith('/auth/csrf'))
      return Response.json({
        user_id: actor.id,
        session_generation: generation,
        csrf_token: 'csrf',
      });
    if (init?.method) return Response.json(account);
    return Response.json({ items: [account], page: 1, page_size: 20, total: 1 });
  });
  render(
    <ApiProvider services={new ApiServices(fetcher)}>
      <AdminSessionGate>
        <AdminAccounts />
      </AdminSessionGate>
    </ApiProvider>,
  );
  return {
    fetcher,
    switchSession: () => {
      generation = 'b'.repeat(43);
    },
  };
}
async function confirmDraft() {
  await userEvent.click(await screen.findByRole('button', { name: `Sửa ${account.email}` }));
  fireEvent.change(screen.getByLabelText('Vai trò', { exact: true }), {
    target: { value: 'ADMIN' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
  await screen.findByRole('button', { name: 'Xác nhận thay đổi' });
}
const focus = () =>
  act(() => {
    window.dispatchEvent(new Event('focus'));
  });

it('preserves the draft and confirmation after revalidating the same session', async () => {
  mount();
  await confirmDraft();
  focus();
  expect(await screen.findByRole('button', { name: 'Xác nhận thay đổi' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Quay lại chỉnh sửa' }));
  expect(screen.getByLabelText('Vai trò', { exact: true })).toHaveValue('ADMIN');
});
it('clears draft and confirmation when the same ADMIN returns with a different session', async () => {
  const fixture = mount();
  await confirmDraft();
  fixture.switchSession();
  focus();
  await screen.findByRole('button', { name: `Sửa ${account.email}` });
  expect(screen.queryByRole('button', { name: 'Xác nhận thay đổi' })).not.toBeInTheDocument();
  expect(screen.queryByRole('form', { name: 'Thông tin tài khoản' })).not.toBeInTheDocument();
  expect(fixture.fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});
it('blocks an old confirmation before dispatch even without a focus event', async () => {
  const fixture = mount();
  await confirmDraft();
  fixture.switchSession();
  await userEvent.click(screen.getByRole('button', { name: 'Xác nhận thay đổi' }));
  await waitFor(() =>
    expect(screen.queryByRole('form', { name: 'Thông tin tài khoản' })).not.toBeInTheDocument(),
  );
  expect(fixture.fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});
it('discards an old session write completion after the same ADMIN replaces the session', async () => {
  const fixture = mount();
  const held = deferred<Response>();
  const original = fixture.fetcher.getMockImplementation()!;
  fixture.fetcher.mockImplementation((path, init) =>
    init?.method === 'PATCH' ? held.promise : original(path, init),
  );
  await confirmDraft();
  await userEvent.click(screen.getByRole('button', { name: 'Xác nhận thay đổi' }));
  await screen.findByText('Đang gửi yêu cầu…');
  await waitFor(() =>
    expect(fixture.fetcher.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true),
  );
  fixture.switchSession();
  focus();
  await screen.findByRole('button', { name: `Sửa ${account.email}` });
  await act(async () => held.resolve(Response.json(account)));
  expect(screen.queryByText('Đã lưu tài khoản.')).not.toBeInTheDocument();
  expect(screen.queryByRole('form', { name: 'Thông tin tài khoản' })).not.toBeInTheDocument();
});
