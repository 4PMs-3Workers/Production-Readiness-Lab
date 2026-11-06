// @vitest-environment node
import { describe, it, expect, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { parseAdminAccount } from '@/services/api/admin-account-mapping';
import {
  accountChanges,
  accountValidation,
  normalizedFields,
} from '@/features/admin/accounts/account-fields';
import { accountFixture as account, adminActor as actor, deferred } from '../admin-fixtures';

const options = { expectedUserId: actor.id, expectedSessionGeneration: actor.session_generation };
const json = (body: unknown, status = 200) => Response.json(body, { status });
function fixture() {
  const fetcher = vi.fn<typeof fetch>(async (path) =>
    String(path).endsWith('/auth/me')
      ? json(actor)
      : String(path).endsWith('/auth/csrf')
        ? json({
            user_id: actor.id,
            csrf_token: 'test-csrf',
            session_generation: actor.session_generation,
          })
        : json(account),
  );
  return { fetcher, services: new ApiServices(fetcher) };
}
describe('admin account contract', () => {
  it.each(['csrf', 'preflight', 'read-before', 'read-after'])(
    'rejects a replaced session of the same ADMIN during %s',
    async (boundary) => {
      const { services, fetcher } = fixture();
      let reads = 0;
      fetcher.mockImplementation(async (path) => {
        if (String(path).endsWith('/auth/csrf'))
          return json({
            user_id: actor.id,
            csrf_token: 'csrf',
            session_generation: boundary === 'csrf' ? 'b'.repeat(43) : actor.session_generation,
          });
        if (String(path).endsWith('/auth/me'))
          return json({
            ...actor,
            session_generation:
              boundary === 'preflight' || boundary === 'read-before' || ++reads === 2
                ? 'b'.repeat(43)
                : actor.session_generation,
          });
        return json({ items: [account], page: 1, page_size: 20, total: 1 });
      });
      const dispatched = vi.fn();
      const result = boundary.startsWith('read')
        ? services.listAdminAccounts({ page: 1 }, options)
        : services.updateAdminAccount(
            account.id,
            { name: 'Changed', expected_updated_at: account.updated_at },
            { ...options, onDispatch: dispatched },
          );
      await expect(result).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' });
      expect(dispatched).not.toHaveBeenCalled();
      expect(fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    },
  );
  it('preserves microseconds and drops unexpected secret fields', () => {
    const mapped = parseAdminAccount({
      ...account,
      password_hash: 'not-for-ui',
      token: 'not-for-ui',
    });
    expect(mapped).toEqual(account);
    expect(mapped.updated_at).toBe('2026-11-05T01:00:00.123456Z');
  });
  it.each([
    { role: 'ROOT' },
    { role: ['STUDENT'] },
    { is_active: 'true' },
    { updated_at: 'bad' },
    { gender: 'bad' },
    { gender: ['MALE'] },
    { faculty: undefined },
  ])('rejects malformed account %j', (invalid) => {
    expect(() => parseAdminAccount({ ...account, ...invalid })).toThrow();
  });
  it('encodes real server filters and verifies the same ADMIN around the read', async () => {
    const { services, fetcher } = fixture();
    fetcher.mockImplementation(async (path) =>
      String(path).endsWith('/auth/me')
        ? json(actor)
        : json({ items: [account], page: 2, page_size: 20, total: 21 }),
    );
    const page = await services.listAdminAccounts(
      { page: 2, role: 'STAFF', isActive: false, search: ' a%_\\ ' },
      options,
    );
    expect(page).toMatchObject({ total: 21, page: 2, items: [account] });
    expect(fetcher.mock.calls.map((c) => c[0])).toEqual([
      '/api/v1/auth/me',
      '/api/v1/admin/accounts?page=2&page_size=20&role=STAFF&is_active=false&search=a%25_%5C',
      '/api/v1/auth/me',
    ]);
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
    });
  });
  it('does not release data if the identity changes after reading', async () => {
    const { services, fetcher } = fixture();
    fetcher
      .mockResolvedValueOnce(json(actor))
      .mockResolvedValueOnce(json({ items: [account], page: 1, page_size: 20, total: 1 }))
      .mockResolvedValueOnce(json({ ...actor, id: account.id }));
    await expect(services.listAdminAccounts({ page: 1 }, options)).rejects.toMatchObject({
      code: 'ACCOUNT_CHANGED',
    });
  });
  it('retains the original cancellation scope across reads and preflight', async () => {
    const { services, fetcher } = fixture();
    const held = deferred<Response>();
    fetcher.mockReturnValueOnce(held.promise);
    const result = services.listAdminAccounts({ page: 1 }, options);
    services.clearSession();
    held.resolve(json(actor));
    await expect(result).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(['create', 'update', 'password'] as const)(
    'uses the exact %s endpoint with CSRF and a frozen version',
    async (kind) => {
      const { services, fetcher } = fixture();
      const dispatched = vi.fn();
      const write = { ...options, onDispatch: dispatched };
      const body = { name: 'Changed', expected_updated_at: account.updated_at };
      const result =
        kind === 'create'
          ? services.createAdminAccount({ ...account, password: '  exact  ' }, write)
          : kind === 'update'
            ? services.updateAdminAccount(account.id, body, write)
            : services.resetAdminAccountPassword(
                account.id,
                { password: '  exact  ', expected_updated_at: account.updated_at },
                write,
              );
      body.expected_updated_at = 'replaced';
      await result;
      const [path, init] = fetcher.mock.calls[2];
      expect(path).toBe(
        `/api/v1/admin/accounts${kind === 'create' ? '' : `/${account.id}${kind === 'password' ? '/password' : ''}`}`,
      );
      expect(init).toMatchObject({
        method: kind === 'update' ? 'PATCH' : 'POST',
        headers: expect.objectContaining({ 'X-CSRF-Token': 'test-csrf' }),
      });
      expect(init?.headers).not.toHaveProperty('Idempotency-Key');
      if (kind !== 'create')
        expect(JSON.parse(String(init?.body)).expected_updated_at).toBe(account.updated_at);
      expect(dispatched).toHaveBeenCalledOnce();
    },
  );
  it.each(['STUDENT', 'STAFF'])(
    'rejects %s during write preflight before dispatch',
    async (role) => {
      const { services, fetcher } = fixture();
      fetcher
        .mockResolvedValueOnce(
          json({
            user_id: actor.id,
            csrf_token: 'csrf',
            session_generation: actor.session_generation,
          }),
        )
        .mockResolvedValueOnce(json({ ...actor, role }));
      const dispatch = vi.fn();
      await expect(
        services.updateAdminAccount(
          account.id,
          { name: 'x', expected_updated_at: account.updated_at },
          { ...options, onDispatch: dispatch },
        ),
      ).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' });
      expect(dispatch).not.toHaveBeenCalled();
      expect(fetcher).toHaveBeenCalledTimes(2);
    },
  );
  it('never sends a write for a different CSRF owner', async () => {
    const { services, fetcher } = fixture();
    fetcher.mockResolvedValueOnce(json({ user_id: account.id, csrf_token: 'csrf' }));
    await expect(
      services.resetAdminAccountPassword(
        account.id,
        { password: 'x', expected_updated_at: account.updated_at },
        options,
      ),
    ).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('constructs only changed fields, retains raw version and avoids empty PATCH', () => {
    expect(accountChanges(account, account)).toBeNull();
    expect(accountChanges(account, { ...account, name: ' Changed ' })).toEqual({
      name: 'Changed',
      expected_updated_at: account.updated_at,
    });
    expect(normalizedFields({ ...account, role: 'STAFF' }).student_code).toBeNull();
  });
  it('mirrors password and email policy without adding complexity rules', () => {
    expect(accountValidation(account, ' ')).toBeNull();
    expect(accountValidation({ ...account, email: 'a@@example.invalid' })).toBeTruthy();
    expect(accountValidation(account, '')).toBeTruthy();
    expect(accountValidation({ ...account, name: '   ' })).toBeTruthy();
    expect(accountValidation({ ...account, name: '😀'.repeat(150) })).toBeNull();
    expect(accountValidation({ ...account, name: '😀'.repeat(151) })).toBeTruthy();
  });
});
