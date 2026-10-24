// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { ApiServices, ProposalRejection } from '@/services/api/client';
import { ApiError, errorMessage } from '@/services/api/errors';
import { parseProposalHistory } from '@/services/api/proposal-mapping';
import type { ProposalCreateBody } from '@/services/proposal-contracts';

const student = {
  id: 'student-a',
  name: 'Sinh viên kiểm thử',
  email: 'student@example.invalid',
  role: 'STUDENT',
};
const staff = { ...student, id: 'staff-a', role: 'STAFF' };
const context = {
  appointment_id: 'appointment-a',
  appointment_status: 'CHANGE_PENDING',
  proposal_version: 4,
  pending_proposal_id: 'proposal-current',
};
const item = {
  id: 'proposal-old',
  appointment_id: 'appointment-a',
  created_by: staff.id,
  created_version: 2,
  proposed_starts_at: '2026-10-27T09:00:00.123456+07:00',
  proposed_ends_at: '2026-10-27T09:45:00.123456+07:00',
  proposed_location: '  Phòng B204  ',
  status: 'SUPERSEDED',
  created_at: '2026-10-24T03:00:00.123456Z',
  expires_at: '2026-10-26T17:00:00Z',
  resolved_at: '2026-10-24T04:00:00Z',
  resolved_by: staff.id,
};
const body: ProposalCreateBody = {
  expected_proposal_version: 4,
  expected_pending_proposal_id: 'proposal-current',
  proposed_starts_at: '2026-10-28T09:00:00.123456+07:00',
  proposed_location: '  Phòng B204  ',
};
const receipt = {
  id: 'proposal-created',
  appointment_id: 'appointment-a',
  created_version: 5,
  created_at: '2026-10-24T03:00:00.123456Z',
  expires_at: '2026-10-26T17:00:00Z',
};
const history = (items: unknown[] = [item], page = 2) => ({
  items,
  page,
  page_size: 20,
  total: 25,
  context,
});
const json = (value: unknown, status = 200) => Response.json(value, { status });
const failure = (code: string, status = 409) =>
  Response.json(
    { error: { code, message: 'SQL credentials consultation text' } },
    { status, headers: { 'X-Request-ID': 'support-proposal-123' } },
  );
const studentOptions = { expectedUserId: student.id, expectedRole: 'STUDENT' as const };
const staffOptions = { expectedUserId: staff.id, expectedRole: 'STAFF' as const };

function writeFetcher(role: 'STAFF' | 'STUDENT', response: () => Response = () => json(receipt)) {
  const owner = role === 'STAFF' ? staff : student;
  return vi.fn<typeof fetch>(async (url) => {
    if (url === '/api/v1/auth/csrf') return json({ csrf_token: 'signed-csrf', user_id: owner.id });
    if (url === '/api/v1/auth/me') return json(owner);
    return response();
  });
}

describe('proposal histories and authorized context', () => {
  it.each(['STUDENT', 'STAFF'] as const)(
    'uses the %s route and maps history without deriving current context from its first item',
    async (role) => {
      const owner = role === 'STAFF' ? staff : student;
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(json(owner))
        .mockResolvedValueOnce(json(history()))
        .mockResolvedValueOnce(json(owner));
      const result = await new ApiServices(fetcher).proposalHistory('appointment-a', 2, {
        expectedUserId: owner.id,
        expectedRole: role,
      });
      expect(result).toMatchObject({
        page: 2,
        pageSize: 20,
        total: 25,
        context: {
          appointmentId: 'appointment-a',
          appointmentStatus: 'CHANGE_PENDING',
          proposalVersion: 4,
          pendingProposalId: 'proposal-current',
        },
        items: [
          {
            id: 'proposal-old',
            createdVersion: 2,
            proposedStartsAt: item.proposed_starts_at,
            proposedLocation: item.proposed_location,
            status: 'SUPERSEDED',
          },
        ],
      });
      expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
        '/api/v1/auth/me',
        `/api/v1${role === 'STAFF' ? '/staff/me' : ''}/appointments/appointment-a/change-proposals?page=2&page_size=20`,
        '/api/v1/auth/me',
      ]);
      expect(fetcher.mock.calls[1][1]).toMatchObject({
        credentials: 'same-origin',
        cache: 'no-store',
        signal: expect.any(AbortSignal),
      });
      expect(fetcher.mock.calls[1][1]?.headers).toEqual({ Accept: 'application/json' });
    },
  );

  it('keeps complete current context on an empty later page and accepts legacy versions', () => {
    expect(parseProposalHistory(history([], 3), 'appointment-a')).toMatchObject({
      items: [],
      page: 3,
      context: { proposalVersion: 4, pendingProposalId: 'proposal-current' },
    });
    expect(
      parseProposalHistory(history([{ ...item, created_version: null }]), 'appointment-a').items[0]
        .createdVersion,
    ).toBeNull();
  });

  it.each([
    null,
    { ...history(), context: undefined },
    { ...history(), context: { ...context, proposal_version: Number.MAX_SAFE_INTEGER + 1 } },
    { ...history(), context: { ...context, pending_proposal_id: null } },
    { ...history(), context: { ...context, appointment_id: 'foreign' } },
    history([{ ...item, appointment_id: 'foreign' }]),
    history([{ ...item, proposed_starts_at: '2026-10-27T09:00:00' }]),
    history([{ ...item, status: 'UNKNOWN_STATUS' }]),
    { ...history(), page_size: 101 },
  ])('rejects malformed/foreign contract data without leaking it: %#', (value) => {
    expect(() => parseProposalHistory(value, 'appointment-a')).toThrow(ApiError);
  });

  it('rejects an account change after receiving the private response', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(student))
      .mockResolvedValueOnce(json(history()))
      .mockResolvedValueOnce(json({ ...student, id: 'student-b' }));
    const services = new ApiServices(fetcher);
    const invalid = vi.fn();
    services.setInvalidSessionHandler(invalid);
    await expect(
      services.proposalHistory('appointment-a', 1, studentOptions),
    ).rejects.toMatchObject({
      code: 'ACCOUNT_CHANGED',
    });
    expect(invalid).toHaveBeenCalledOnce();
  });

  it('aborts the whole read sequence when its session scope is cleared', async () => {
    let complete!: (response: Response) => void;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(student))
      .mockImplementationOnce(() => new Promise((resolve) => (complete = resolve)));
    const services = new ApiServices(fetcher);
    const pending = services.proposalHistory('appointment-a', 1, studentOptions);
    await vi.waitFor(() => expect(complete).toBeTypeOf('function'));
    services.clearSession();
    complete(json(history()));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe('proposal writes preserve exact intention and immutable responses', () => {
  it.each([200, 201])(
    'maps a %i creation receipt without assigning a current status',
    async (status) => {
      const fetcher = writeFetcher('STAFF', () => json(receipt, status));
      const onDispatch = vi.fn();
      const result = await new ApiServices(fetcher).createProposal('appointment-a', body, 'Key-A', {
        ...staffOptions,
        onDispatch,
      });
      expect(result).toEqual({
        id: receipt.id,
        appointmentId: receipt.appointment_id,
        createdVersion: receipt.created_version,
        createdAt: receipt.created_at,
        expiresAt: receipt.expires_at,
      });
      expect(result).not.toHaveProperty('status');
      expect(onDispatch).toHaveBeenCalledOnce();
      expect(fetcher.mock.calls[2]).toEqual([
        '/api/v1/staff/me/appointments/appointment-a/change-proposals',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify(body),
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'X-CSRF-Token': 'signed-csrf',
            'Idempotency-Key': 'Key-A',
          },
        }),
      ]);
    },
  );

  it('captures both preconditions, microseconds and exact location before auth awaits', async () => {
    const fetcher = writeFetcher('STAFF');
    const mutable = { ...body, unrelated: 'do not send' };
    const submitted = new ApiServices(fetcher).createProposal(
      'appointment-a',
      mutable,
      'key',
      staffOptions,
    );
    mutable.expected_proposal_version = 100;
    mutable.expected_pending_proposal_id = 'new-pending';
    mutable.proposed_location = 'changed during auth';
    await submitted;
    expect(fetcher.mock.calls[2][1]?.body).toBe(JSON.stringify(body));
  });

  it('sends explicit nulls to clear location and represent no pending proposal', async () => {
    const fetcher = writeFetcher('STAFF');
    await new ApiServices(fetcher).createProposal(
      'appointment-a',
      { ...body, expected_pending_proposal_id: null, proposed_location: null },
      'key',
      staffOptions,
    );
    expect(JSON.parse(fetcher.mock.calls[2][1]?.body as string)).toEqual({
      ...body,
      expected_pending_proposal_id: null,
      proposed_location: null,
    });
  });

  it.each(['accept', 'decline'] as const)(
    'sends {} for %s and maps the saved resolution',
    async (action) => {
      const resolution = {
        id: 'proposal-current',
        appointment_id: 'appointment-a',
        status: action === 'accept' ? 'ACCEPTED' : 'DECLINED',
        resolved_at: '2026-10-24T04:00:00.123456Z',
        resolved_by: student.id,
      };
      const fetcher = writeFetcher('STUDENT', () => json(resolution));
      const result = await new ApiServices(fetcher).decideProposal(
        'appointment-a',
        'proposal-current',
        action,
        studentOptions,
      );
      expect(result).toEqual({
        id: resolution.id,
        appointmentId: resolution.appointment_id,
        status: resolution.status,
        resolvedAt: resolution.resolved_at,
        resolvedBy: student.id,
      });
      expect(fetcher.mock.calls[2][0]).toBe(
        `/api/v1/appointments/appointment-a/change-proposals/proposal-current/${action}`,
      );
      expect(fetcher.mock.calls[2][1]?.body).toBe('{}');
      expect(fetcher.mock.calls[2][1]?.headers).not.toHaveProperty('Idempotency-Key');
    },
  );

  it('does not dispatch a business write under a different role or owner', async () => {
    const fetcher = writeFetcher('STUDENT');
    const onDispatch = vi.fn();
    await expect(
      new ApiServices(fetcher).createProposal('appointment-a', body, 'key', {
        expectedUserId: student.id,
        onDispatch,
      }),
    ).rejects.toMatchObject({ code: 'ACCOUNT_CHANGED' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(onDispatch).not.toHaveBeenCalled();
  });

  it('preserves an invalid 2xx receipt as an uncertain service failure', async () => {
    const fetcher = writeFetcher('STAFF', () => json({ ...receipt, appointment_id: 'foreign' }));
    const error = await new ApiServices(fetcher)
      .createProposal('appointment-a', body, 'key', staffOptions)
      .catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(ProposalRejection);
    expect((error as ApiError).unknownOutcome).toBe(true);
  });

  it.each([
    'PROPOSAL_PRECONDITION_FAILED',
    'APPOINTMENT_STATE_CONFLICT',
    'PROPOSAL_EXPIRED',
    'PROPOSAL_NO_CHANGE',
    'STAFF_UNAVAILABLE',
    'STUDENT_UNAVAILABLE',
    'BOOKING_DATE_INVALID',
  ])('marks post-dispatch %s as a definitive rejection and sanitizes details', async (code) => {
    const fetcher = writeFetcher('STAFF', () => failure(code));
    const error = await new ApiServices(fetcher)
      .createProposal('appointment-a', body, 'key', staffOptions)
      .catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ProposalRejection);
    expect(error).toMatchObject({ code, requestId: 'support-proposal-123' });
    expect(errorMessage(error)).not.toContain('SQL credentials');
  });

  it('marks a saved-decision conflict as definitive for the original proposal', async () => {
    const fetcher = writeFetcher('STUDENT', () => failure('PROPOSAL_STATE_CONFLICT'));
    await expect(
      new ApiServices(fetcher).decideProposal(
        'appointment-a',
        'proposal-current',
        'accept',
        studentOptions,
      ),
    ).rejects.toBeInstanceOf(ProposalRejection);
  });

  it.each([
    ['IDEMPOTENCY_CONFLICT', 409],
    ['VALIDATION_ERROR', 422],
    ['INVALID_SESSION', 401],
    ['NOT_FOUND', 404],
    ['SERVICE_UNAVAILABLE', 503],
  ] as const)('keeps %s distinct from a replay-aware business rejection', async (code, status) => {
    const fetcher = writeFetcher('STAFF', () => failure(code, status));
    const error = await new ApiServices(fetcher)
      .createProposal('appointment-a', body, 'key', staffOptions)
      .catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(ProposalRejection);
    expect(error).toMatchObject({ code });
  });

  it('never classifies preflight conflicts as proof an earlier unknown operation failed', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => failure('PROPOSAL_PRECONDITION_FAILED'));
    const onDispatch = vi.fn();
    const error = await new ApiServices(fetcher)
      .createProposal('appointment-a', body, 'key', { ...staffOptions, onDispatch })
      .catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(ProposalRejection);
    expect(onDispatch).not.toHaveBeenCalled();
  });

  it('does not dispatch requests with an already aborted caller signal', async () => {
    const controller = new AbortController();
    controller.abort();
    const fetcher = vi.fn<typeof fetch>();
    await expect(
      new ApiServices(fetcher).createProposal('appointment-a', body, 'key', {
        ...staffOptions,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
