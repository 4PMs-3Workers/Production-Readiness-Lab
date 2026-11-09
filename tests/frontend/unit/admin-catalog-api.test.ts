// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { parseAdminPurpose, parseAdminPurposePage } from '@/services/api/admin-catalog-mapping';
import { purposeDraft, purposeErrors } from '@/features/admin/purposes/purpose-fields';
import { adminActor as actor, deferred } from '../admin-fixtures';
import { purposeFixture as purpose } from '../admin-configuration-fixtures';

const options = { expectedUserId: actor.id, expectedSessionGeneration: actor.session_generation };
const json = (value: unknown, status = 200) => Response.json(value, { status });
function fixture() {
  const fetcher = vi.fn<typeof fetch>(async (path, init) => {
    if (String(path).endsWith('/auth/me')) return json(actor);
    if (String(path).endsWith('/auth/csrf'))
      return json({
        user_id: actor.id,
        session_generation: actor.session_generation,
        csrf_token: 'csrf',
      });
    if (init?.method)
      return json(
        { ...purpose, ...JSON.parse(String(init.body)) },
        init.method === 'POST' ? 201 : 200,
      );
    if (String(path).includes('?'))
      return json({ items: [purpose], page: 2, page_size: 20, total: 21 });
    return json(purpose);
  });
  return { fetcher, services: new ApiServices(fetcher) };
}
describe('ADMIN purpose API', () => {
  it('uses server paging/active filters and verifies identity on both sides of GET', async () => {
    const { services, fetcher } = fixture();
    expect(await services.listAdminPurposes({ page: 2, isActive: false }, options)).toMatchObject({
      page: 2,
      total: 21,
      items: [purpose],
    });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/auth/me',
      '/api/v1/admin/purposes?page=2&page_size=20&is_active=false',
      '/api/v1/auth/me',
    ]);
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
    });
  });
  it('reads inactive purposes directly by stable ID', async () => {
    const { services, fetcher } = fixture();
    await services.getAdminPurpose(purpose.id, options);
    expect(fetcher.mock.calls[1][0]).toBe(`/api/v1/admin/purposes/${purpose.id}`);
  });
  it.each(['create', 'replace'] as const)(
    'sends a frozen full %s request without unexpected fields',
    async (kind) => {
      const { services, fetcher } = fixture();
      const dispatch = vi.fn();
      const body = { ...purpose, name: '  Nouveau  ', expected_updated_at: purpose.updated_at };
      const result =
        kind === 'create'
          ? services.createAdminPurpose(body, { ...options, onDispatch: dispatch })
          : services.replaceAdminPurpose(purpose.id, body, { ...options, onDispatch: dispatch });
      body.name = 'Changed during preflight';
      body.expected_updated_at = 'lost';
      await result;
      const [path, init] = fetcher.mock.calls[2];
      expect(path).toBe(`/api/v1/admin/purposes${kind === 'replace' ? `/${purpose.id}` : ''}`);
      expect(init?.method).toBe(kind === 'create' ? 'POST' : 'PUT');
      const sent = JSON.parse(String(init?.body));
      expect(sent).toEqual({
        ...(kind === 'create'
          ? { code: purpose.code }
          : { expected_updated_at: purpose.updated_at }),
        name: 'Nouveau',
        duration_minutes: 30,
        requires_free_text: true,
        is_active: true,
      });
      expect(init?.headers).toMatchObject({ 'X-CSRF-Token': 'csrf' });
      expect(init?.headers).not.toHaveProperty('Idempotency-Key');
      expect(dispatch).toHaveBeenCalledOnce();
    },
  );
  it.each(['csrf', 'read-before', 'read-after'] as const)(
    'rejects a same-ADMIN replacement at %s',
    async (boundary) => {
      const { services, fetcher } = fixture();
      let reads = 0;
      const original = fetcher.getMockImplementation()!;
      fetcher.mockImplementation(async (path, init) => {
        if (String(path).endsWith('/auth/me')) {
          reads++;
          return json({
            ...actor,
            session_generation:
              boundary === 'read-before' || (boundary === 'read-after' && reads === 2)
                ? 'b'.repeat(43)
                : actor.session_generation,
          });
        }
        if (String(path).endsWith('/auth/csrf') && boundary === 'csrf')
          return json({
            user_id: actor.id,
            session_generation: 'b'.repeat(43),
            csrf_token: 'csrf',
          });
        return original(path, init);
      });
      const result =
        boundary === 'csrf'
          ? services.createAdminPurpose(purpose, options)
          : services.getAdminPurpose(purpose.id, options);
      await expect(result).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' });
      expect(fetcher.mock.calls.some(([, init]) => init?.method)).toBe(false);
    },
  );
  it('retains the original abort scope across verification', async () => {
    const { services, fetcher } = fixture();
    const held = deferred<Response>();
    fetcher.mockReturnValueOnce(held.promise);
    const result = services.getAdminPurpose(purpose.id, options);
    services.clearSession();
    held.resolve(json(actor));
    await expect(result).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('maps only public fields and keeps the raw microsecond revision', () => {
    expect(parseAdminPurpose({ ...purpose, password_hash: 'discard', token: 'discard' })).toEqual(
      purpose,
    );
  });
  it.each([
    { is_active: 'false' },
    { requires_free_text: 1 },
    { duration_minutes: 0 },
    { duration_minutes: 1.5 },
    { duration_minutes: 32768 },
    { updated_at: '2026-02-30T00:00:00Z' },
    { id: [] },
    { code: 'lower' },
    { name: '' },
  ])('rejects malformed purpose %j', (patch) => {
    expect(() => parseAdminPurpose({ ...purpose, ...patch })).toThrow();
  });
  it('rejects malformed page metadata', () =>
    expect(() =>
      parseAdminPurposePage({ items: [], page: '1', page_size: 20, total: 0 }),
    ).toThrow());
  it('does not accept a successful receipt for another purpose or different state', async () => {
    const { services, fetcher } = fixture();
    fetcher.mockImplementation(async (path) =>
      String(path).endsWith('/auth/csrf')
        ? json({
            user_id: actor.id,
            session_generation: actor.session_generation,
            csrf_token: 'csrf',
          })
        : String(path).endsWith('/auth/me')
          ? json(actor)
          : json({ ...purpose, name: 'Wrong' }),
    );
    await expect(services.createAdminPurpose(purpose, options)).rejects.toMatchObject({
      unknownOutcome: true,
    });
  });
  it('validates code, integer boundaries and Unicode name length without inventing step rules', () => {
    const draft = purposeDraft(purpose);
    expect(purposeErrors({ ...draft, name: '😀'.repeat(150), duration_minutes: '32767' })).toEqual(
      {},
    );
    expect(
      purposeErrors({ ...draft, name: '😀'.repeat(151), duration_minutes: '1.5', code: '1lower' }),
    ).toHaveProperty('duration_minutes');
    expect(purposeErrors({ ...draft, duration_minutes: '1' })).toEqual({});
  });
});
