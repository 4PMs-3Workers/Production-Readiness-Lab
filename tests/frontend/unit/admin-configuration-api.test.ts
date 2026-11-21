// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { parseAdminConfiguration, policyMatches } from '@/services/api/admin-configuration-mapping';
import {
  configurationDraft,
  configurationErrors,
  draftPolicy,
} from '@/features/admin/booking-configuration/configuration-fields';
import { adminActor as actor, deferred } from '../admin-fixtures';
import { configurationFixture as configuration } from '../admin-configuration-fixtures';

const options = { expectedUserId: actor.id, expectedSessionGeneration: actor.session_generation };
const csrf = {
  user_id: actor.id,
  session_generation: actor.session_generation,
  csrf_token: 'csrf',
};
function fixture() {
  const fetcher = vi.fn<typeof fetch>(async (path, init) =>
    Response.json(
      String(path).endsWith('/auth/csrf')
        ? csrf
        : String(path).endsWith('/auth/me')
          ? actor
          : init?.method
            ? { ...JSON.parse(String(init.body)), updated_at: configuration.updated_at }
            : configuration,
    ),
  );
  return { fetcher, services: new ApiServices(fetcher) };
}
describe('ADMIN atomic booking configuration', () => {
  it('reads the singleton without query and checks the ADMIN before and after', async () => {
    const { services, fetcher } = fixture();
    expect(await services.getAdminBookingConfiguration(options)).toEqual(configuration);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/auth/me',
      '/api/v1/admin/booking-configuration',
      '/api/v1/auth/me',
    ]);
  });
  it('deep snapshots the entire policy/windows and preserves seconds/version across preflight', async () => {
    const { services, fetcher } = fixture();
    const held = deferred<Response>();
    fetcher.mockReturnValueOnce(held.promise);
    const body = {
      ...configuration,
      windows: configuration.windows.map((w) => ({ ...w })),
      expected_updated_at: configuration.updated_at,
    };
    const result = services.replaceAdminBookingConfiguration(body, options);
    body.windows[0].opens_at = '09:00:00';
    body.timezone = 'UTC';
    body.expected_updated_at = 'replaced';
    held.resolve(Response.json(csrf));
    expect(await result).toEqual(configuration);
    const [path, init] = fetcher.mock.calls[2];
    expect(path).toBe('/api/v1/admin/booking-configuration');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toEqual({
      timezone: configuration.timezone,
      start_step_minutes: 15,
      booking_horizon_days: 7,
      windows: configuration.windows,
      expected_updated_at: configuration.updated_at,
    });
  });
  it('accepts matching-state receipts with an old precondition', async () => {
    const { services } = fixture();
    await expect(
      services.replaceAdminBookingConfiguration(
        { ...configuration, expected_updated_at: '2020-01-01T00:00:00.000001Z' },
        options,
      ),
    ).resolves.toEqual(configuration);
  });
  it.each([
    { start_step_minutes: '15' },
    { booking_horizon_days: 0 },
    { timezone: [] },
    { windows: null },
    { updated_at: '2026-11-08T25:00:00Z' },
    { windows: [{ weekday: 8, opens_at: '08:00:00', closes_at: '09:00:00' }] },
    { windows: [{ weekday: 1, opens_at: '08:00', closes_at: '09:00' }] },
    { windows: [{ weekday: 1, opens_at: '08:00:00', closes_at: '08:00:00' }] },
    { windows: [configuration.windows[0], configuration.windows[0]] },
  ])('rejects invalid configuration %j', (patch) =>
    expect(() => parseAdminConfiguration({ ...configuration, ...patch })).toThrow(),
  );
  it('preserves adjacent windows, accepts all-week-closed and drops unknown fields', () => {
    expect(parseAdminConfiguration({ ...configuration, private: 'discard' })).toEqual(
      configuration,
    );
    expect(parseAdminConfiguration({ ...configuration, windows: [] }).windows).toEqual([]);
    expect(
      policyMatches(configuration, {
        ...configuration,
        windows: [...configuration.windows].reverse(),
      }),
    ).toBe(true);
  });
  it('classifies malformed successful PUT receipts as unknown', async () => {
    const { services, fetcher } = fixture();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (path, init) =>
      init?.method ? Response.json({ ...configuration, windows: [] }) : original(path, init),
    );
    await expect(
      services.replaceAdminBookingConfiguration(
        { ...configuration, expected_updated_at: configuration.updated_at },
        options,
      ),
    ).rejects.toMatchObject({ unknownOutcome: true });
  });
  it.each(['timezone', 'start_step_minutes', 'booking_horizon_days'] as const)(
    'validates %s without changing other full-resource fields',
    (field) => {
      const draft = configurationDraft(configuration);
      expect(
        configurationErrors({ ...draft, [field]: field === 'timezone' ? '' : '0' }),
      ).toHaveProperty(field);
      expect(draftPolicy(draft).windows).toEqual(configuration.windows);
    },
  );
  it('validates overlap but permits adjacent/seconds/empty windows and large duration-independent steps', () => {
    const draft = configurationDraft(configuration);
    expect(configurationErrors(draft)).toEqual({});
    expect(configurationErrors({ ...draft, windows: [], start_step_minutes: '32767' })).toEqual({});
    expect(
      configurationErrors({
        ...draft,
        windows: [...draft.windows, { ...draft.windows[0], key: 'duplicate' }],
      }),
    ).toHaveProperty('windows');
    expect(
      draftPolicy({ ...draft, windows: [{ ...draft.windows[0], opens_at: '08:00' }] }).windows[0]
        .opens_at,
    ).toBe('08:00:00');
  });
});
