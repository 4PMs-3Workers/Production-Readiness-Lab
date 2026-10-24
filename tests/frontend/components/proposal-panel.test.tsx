import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProposalPanel } from '@/features/proposals/proposal-panel';
import type { ProposalAppointment } from '@/features/proposals/proposal-form';
import { ProposalIntentStore } from '@/services/api/proposal-intent';
import { StaffChangeIntentStore } from '@/services/api/staff-change-intent';
import { ApiError } from '@/services/api/errors';
import { ProposalRejection } from '@/services/api/client';
import type { ProposalHistoryItem, ProposalHistoryPage } from '@/services/proposal-contracts';

const STAFF = '11111111-1111-4111-8111-111111111111';
const STUDENT = '22222222-2222-4222-8222-222222222222';
const OTHER = '33333333-3333-4333-8333-333333333333';
const APPOINTMENT = '44444444-4444-4444-8444-444444444444';
const PENDING = '55555555-5555-4555-8555-555555555555';
const REPLACEMENT = '66666666-6666-4666-8666-666666666666';

const mock = vi.hoisted(() => ({
  services: {
    proposalHistory: vi.fn(),
    createProposal: vi.fn(),
    decideProposal: vi.fn(),
  },
  userId: '',
  role: 'STAFF' as 'STUDENT' | 'STAFF',
}));
let proposalIntents: ProposalIntentStore;
const staffChanges = new StaffChangeIntentStore();
vi.mock('@/features/api/api-provider', () => ({
  useApi: () => ({
    services: mock.services,
    proposalIntents,
    staffChanges,
    revision: 0,
    session: { status: 'authenticated', user: { id: mock.userId, role: mock.role } },
  }),
}));

const appointment: ProposalAppointment = {
  id: APPOINTMENT,
  startsAt: '2026-10-28T01:00:00.123456Z',
  endsAt: '2026-10-28T01:45:00.123456Z',
  durationMinutes: 45,
  location: '  Phòng B204  ',
  status: 'UPCOMING',
};
const pending: ProposalHistoryItem = {
  id: PENDING,
  appointmentId: APPOINTMENT,
  createdBy: STAFF,
  createdVersion: 7,
  proposedStartsAt: '2026-10-29T03:00:00Z',
  proposedEndsAt: '2026-10-29T03:45:00Z',
  proposedLocation: 'Phòng đề xuất',
  status: 'PENDING',
  createdAt: '2026-10-24T02:00:00Z',
  expiresAt: '2026-10-27T17:00:00Z',
  resolvedAt: null,
  resolvedBy: null,
};

function history(overrides: Partial<ProposalHistoryPage> = {}): ProposalHistoryPage {
  return {
    items: [],
    page: 1,
    pageSize: 20,
    total: 0,
    context: {
      appointmentId: APPOINTMENT,
      appointmentStatus: 'UPCOMING',
      proposalVersion: 6,
      pendingProposalId: null,
    },
    ...overrides,
  };
}

function withPending(item = pending, overrides: Partial<ProposalHistoryPage> = {}) {
  return history({
    items: [item],
    total: 1,
    context: {
      appointmentId: APPOINTMENT,
      appointmentStatus: 'CHANGE_PENDING',
      proposalVersion: item.createdVersion!,
      pendingProposalId: item.id,
    },
    ...overrides,
  });
}

function asStudent() {
  mock.userId = STUDENT;
  mock.role = 'STUDENT';
  proposalIntents.bindAccount(STUDENT, 'STUDENT');
}

function panel(onRefresh = vi.fn().mockResolvedValue(appointment)) {
  return {
    onRefresh,
    ...render(
      <ProposalPanel
        appointment={appointment}
        role={mock.role}
        timezone="Asia/Ho_Chi_Minh"
        onRefresh={onRefresh}
      />,
    ),
  };
}

async function reviewStaff(locationMode: 'keep' | 'change' | 'clear' = 'keep') {
  const controls = userEvent.setup();
  await controls.click(await screen.findByRole('button', { name: 'Đề xuất đổi lịch' }));
  if (locationMode === 'keep')
    fireEvent.change(screen.getByLabelText('Giờ đề xuất'), { target: { value: '10:30' } });
  await controls.selectOptions(screen.getByLabelText('Thay đổi địa điểm'), locationMode);
  if (locationMode === 'change')
    await controls.type(screen.getByLabelText('Địa điểm đề xuất'), '  B301  ');
  await controls.click(screen.getByRole('button', { name: 'Xem lại đề xuất' }));
  return controls;
}

beforeEach(() => {
  vi.resetAllMocks();
  window.sessionStorage.clear();
  staffChanges.clearAll();
  proposalIntents = new ProposalIntentStore();
  mock.userId = STAFF;
  mock.role = 'STAFF';
  proposalIntents.bindAccount(STAFF, 'STAFF');
  mock.services.proposalHistory.mockResolvedValue(history());
  mock.services.createProposal.mockResolvedValue({
    id: PENDING,
    appointmentId: APPOINTMENT,
    createdVersion: 7,
    createdAt: pending.createdAt,
    expiresAt: pending.expiresAt,
  });
  mock.services.decideProposal.mockResolvedValue({
    id: PENDING,
    appointmentId: APPOINTMENT,
    status: 'ACCEPTED',
    resolvedAt: '2026-10-24T03:00:00Z',
    resolvedBy: STUDENT,
  });
});

describe('proposal panel confirmations and authoritative reads', () => {
  it('lets a slow authorized read finish instead of aborting it on every polling interval', async () => {
    vi.useFakeTimers();
    let complete!: (value: ProposalHistoryPage) => void;
    let readSignal!: AbortSignal;
    mock.services.proposalHistory.mockImplementationOnce((_id, _page, options) => {
      readSignal = options.signal;
      return new Promise((resolve) => {
        complete = resolve;
      });
    });
    const view = panel();
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(mock.services.proposalHistory).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(readSignal.aborted).toBe(false);
      expect(mock.services.proposalHistory).toHaveBeenCalledTimes(1);
      await act(async () => {
        complete(history());
      });
      expect(screen.getByText('Hiện không có đề xuất đang chờ phản hồi.')).toBeVisible();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(mock.services.proposalHistory).toHaveBeenCalledTimes(2);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('traps confirmation keyboard focus and restores the review trigger on Escape without posting', async () => {
    panel();
    const controls = await reviewStaff();
    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Xác nhận gửi đề xuất' });
    const back = within(dialog).getByRole('button', { name: 'Quay lại' });
    expect(confirm).toHaveFocus();
    await controls.tab({ shift: true });
    expect(back).toHaveFocus();
    await controls.tab();
    expect(confirm).toHaveFocus();
    await controls.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Xem lại đề xuất' })).toHaveFocus();
    expect(mock.services.createProposal).not.toHaveBeenCalled();
  });

  it.each(['STAFF', 'STUDENT'] as const)(
    'shows %s persistence failure inside its confirmation and never dispatches',
    async (role) => {
      if (role === 'STUDENT') {
        asStudent();
        mock.services.proposalHistory.mockResolvedValue(withPending());
      }
      panel();
      const controls = role === 'STAFF' ? await reviewStaff() : userEvent.setup();
      if (role === 'STUDENT')
        await controls.click(await screen.findByRole('button', { name: 'Chấp nhận đề xuất' }));
      const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException('Quota exceeded', 'QuotaExceededError');
      });
      const dialog = screen.getByRole('dialog');
      await controls.click(
        within(dialog).getByRole('button', {
          name: role === 'STAFF' ? 'Xác nhận gửi đề xuất' : 'Xác nhận chấp nhận',
        }),
      );
      expect(within(dialog).getByRole('alert')).toHaveTextContent(
        /Không thể lưu hoặc đọc thao tác/,
      );
      expect(mock.services.createProposal).not.toHaveBeenCalled();
      expect(mock.services.decideProposal).not.toHaveBeenCalled();
      storage.mockRestore();
      await controls.click(within(dialog).getByRole('button', { name: 'Quay lại' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    },
  );

  it('sends exact server preconditions and kept location only after confirmation and disables duplicate clicks', async () => {
    let finish!: (value: unknown) => void;
    mock.services.createProposal.mockImplementation(
      (_id, _body, _key, options) =>
        new Promise((resolve) => {
          options.onDispatch();
          finish = resolve;
        }),
    );
    const { onRefresh } = panel();
    const controls = await reviewStaff();
    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Xác nhận gửi đề xuất' });
    expect(confirm).toHaveFocus();
    expect(mock.services.createProposal).not.toHaveBeenCalled();
    expect(within(dialog).getAllByText('45 phút')).toHaveLength(2);

    await controls.click(confirm);
    expect(confirm).toBeDisabled();
    await controls.click(confirm);
    expect(mock.services.createProposal).toHaveBeenCalledTimes(1);
    const [id, body, key, options] = mock.services.createProposal.mock.calls[0];
    expect(id).toBe(APPOINTMENT);
    expect(body).toEqual({
      expected_proposal_version: 6,
      expected_pending_proposal_id: null,
      proposed_starts_at: '2026-10-28T10:30:00+07:00',
      proposed_location: appointment.location,
    });
    expect(key).toMatch(/^[a-f0-9-]{36}$/);
    expect(options.expectedUserId).toBe(STAFF);
    expect(proposalIntents.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('submitting');

    await act(async () => finish({ id: PENDING }));
    await waitFor(() => expect(proposalIntents.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull());
    expect(onRefresh).toHaveBeenCalledWith(expect.any(AbortSignal));
    // A historical receipt does not fabricate a pending proposal absent from the current read.
    expect(screen.getByText('Hiện không có đề xuất đang chờ phản hồi.')).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it.each([
    ['clear', null],
    ['change', '  B301  '],
  ] as const)(
    'distinguishes %s location from keeping it and preserves a location-only start exactly',
    async (mode, location) => {
      panel();
      const controls = await reviewStaff(mode);
      await controls.click(screen.getByRole('button', { name: 'Xác nhận gửi đề xuất' }));
      await waitFor(() => expect(mock.services.createProposal).toHaveBeenCalledTimes(1));
      expect(mock.services.createProposal.mock.calls[0][1]).toMatchObject({
        proposed_starts_at: appointment.startsAt,
        proposed_location: location,
      });
    },
  );

  it('obtains pending context on an empty later page and targets its ID rather than a first history item', async () => {
    asStudent();
    const terminal = { ...pending, id: REPLACEMENT, status: 'SUPERSEDED' as const };
    const first = withPending(pending, { items: [terminal, pending], total: 21 });
    mock.services.proposalHistory.mockImplementation(async (_id, page) =>
      page === 1 ? first : { ...first, page: 2, items: [] },
    );
    const { onRefresh } = panel();
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('button', { name: 'Trang sau' }));
    expect(await screen.findByText('Chưa có đề xuất trên trang này.')).toBeVisible();
    await controls.click(screen.getByRole('button', { name: 'Chấp nhận đề xuất' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(`Mã đề xuất: ${PENDING}`)).toBeVisible();
    expect(mock.services.decideProposal).not.toHaveBeenCalled();
    await controls.click(within(dialog).getByRole('button', { name: 'Xác nhận chấp nhận' }));
    await waitFor(() =>
      expect(mock.services.decideProposal).toHaveBeenCalledWith(
        APPOINTMENT,
        PENDING,
        'accept',
        expect.objectContaining({ expectedUserId: STUDENT }),
      ),
    );
    expect(onRefresh).toHaveBeenCalled();
    expect(mock.services.proposalHistory).toHaveBeenCalledWith(APPOINTMENT, 2, expect.any(Object));
  });

  it('explains that decline cancels the appointment before sending the same target action', async () => {
    asStudent();
    mock.services.proposalHistory.mockResolvedValue(withPending());
    panel();
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('button', { name: 'Từ chối đề xuất' }));
    const dialog = screen.getByRole('dialog', { name: 'Từ chối đề xuất và hủy cuộc hẹn?' });
    expect(within(dialog).getByText(/hủy toàn bộ cuộc hẹn và giải phóng lịch gốc/)).toBeVisible();
    expect(mock.services.decideProposal).not.toHaveBeenCalled();
    await controls.click(within(dialog).getByRole('button', { name: 'Xác nhận từ chối' }));
    await waitFor(() =>
      expect(mock.services.decideProposal).toHaveBeenCalledWith(
        APPOINTMENT,
        PENDING,
        'decline',
        expect.any(Object),
      ),
    );
  });
});

describe('proposal recovery through component lifecycle', () => {
  it('recovers a lost creation response after remount with the original key and body despite newer server context', async () => {
    mock.services.createProposal.mockImplementationOnce(async (_id, _body, _key, options) => {
      options.onDispatch();
      throw new ApiError('NETWORK_ERROR');
    });
    const view = panel();
    const controls = await reviewStaff('clear');
    await controls.click(screen.getByRole('button', { name: 'Xác nhận gửi đề xuất' }));
    await waitFor(() =>
      expect(proposalIntents.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('unknown'),
    );
    const first = mock.services.createProposal.mock.calls[0];
    view.unmount();

    proposalIntents = new ProposalIntentStore();
    proposalIntents.bindAccount(STAFF, 'STAFF');
    mock.services.proposalHistory.mockResolvedValue(
      withPending({ ...pending, id: REPLACEMENT, createdVersion: 20 }),
    );
    panel();
    const retry = await screen.findByRole('button', { name: 'Thử lại thao tác đã gửi' });
    expect(screen.queryByRole('button', { name: 'Thay thế đề xuất' })).toBeDisabled();
    await controls.click(retry);
    await waitFor(() => expect(mock.services.createProposal).toHaveBeenCalledTimes(2));
    const second = mock.services.createProposal.mock.calls[1];
    expect(second.slice(0, 3)).toEqual(first.slice(0, 3));
    expect(second[1].expected_proposal_version).toBe(6);
    expect(second[1].expected_pending_proposal_id).toBeNull();
  });

  it('keeps a confirmed operation until a failed current-detail read succeeds and never resubmits it', async () => {
    const onRefresh = vi
      .fn()
      .mockRejectedValueOnce(new ApiError('NETWORK_ERROR'))
      .mockResolvedValue(appointment);
    panel(onRefresh);
    const controls = await reviewStaff('clear');
    await controls.click(screen.getByRole('button', { name: 'Xác nhận gửi đề xuất' }));
    await waitFor(() =>
      expect(proposalIntents.get(STAFF, 'STAFF', APPOINTMENT)?.phase).toBe('confirmed'),
    );
    expect(await screen.findByText(/Cần tải lại lịch và đề xuất/)).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Thử lại thao tác đã gửi' }),
    ).not.toBeInTheDocument();
    await controls.click(screen.getByRole('button', { name: 'Làm mới đề xuất' }));
    await waitFor(() => expect(proposalIntents.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull());
    expect(mock.services.createProposal).toHaveBeenCalledTimes(1);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it('requires a new explicit confirmation after a stale precondition rejection instead of silently rebasing', async () => {
    mock.services.createProposal.mockRejectedValueOnce(
      new ProposalRejection('PROPOSAL_PRECONDITION_FAILED', 409),
    );
    panel();
    const controls = await reviewStaff('clear');
    mock.services.proposalHistory.mockResolvedValue(
      history({
        context: { ...history().context, proposalVersion: 9 },
      }),
    );
    await controls.click(screen.getByRole('button', { name: 'Xác nhận gửi đề xuất' }));
    await waitFor(() => expect(proposalIntents.get(STAFF, 'STAFF', APPOINTMENT)).toBeNull());
    expect(mock.services.createProposal).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await reviewStaff('clear');
    expect(mock.services.createProposal).toHaveBeenCalledTimes(1);
    await controls.click(screen.getByRole('button', { name: 'Xác nhận gửi đề xuất' }));
    await waitFor(() => expect(mock.services.createProposal).toHaveBeenCalledTimes(2));
    const [first, second] = mock.services.createProposal.mock.calls;
    expect(first[1].expected_proposal_version).toBe(6);
    expect(second[1].expected_proposal_version).toBe(9);
    expect(second[2]).not.toBe(first[2]);
  });

  it('retries an unresolved student action on the original proposal even after another proposal becomes pending', async () => {
    asStudent();
    const saved = proposalIntents.prepareDecision(STUDENT, APPOINTMENT, PENDING, 'decline');
    proposalIntents.markUnknown(saved.operation, proposalIntents.begin(saved.operation)!);
    mock.services.proposalHistory.mockResolvedValue(
      withPending({ ...pending, id: REPLACEMENT, createdVersion: 8 }),
    );
    panel();
    const controls = userEvent.setup();
    const retry = await screen.findByRole('button', { name: 'Thử lại thao tác đã gửi' });
    expect(await screen.findByRole('button', { name: 'Chấp nhận đề xuất' })).toBeDisabled();
    await controls.click(retry);
    await waitFor(() =>
      expect(mock.services.decideProposal).toHaveBeenCalledWith(
        APPOINTMENT,
        PENDING,
        'decline',
        expect.any(Object),
      ),
    );
    expect(mock.services.decideProposal).toHaveBeenCalledTimes(1);
  });

  it('hides another account recovery and ignores an old in-flight response after account switch', async () => {
    asStudent();
    mock.services.proposalHistory.mockResolvedValue(withPending());
    let resolveDecision!: (value: unknown) => void;
    let oldSignal!: AbortSignal;
    mock.services.decideProposal.mockImplementation(
      (_id, _proposal, _action, options) =>
        new Promise((resolve) => {
          oldSignal = options.signal;
          options.onDispatch();
          resolveDecision = resolve;
        }),
    );
    const view = panel();
    const controls = userEvent.setup();
    await controls.click(await screen.findByRole('button', { name: 'Chấp nhận đề xuất' }));
    await controls.click(screen.getByRole('button', { name: 'Xác nhận chấp nhận' }));
    expect(proposalIntents.get(STUDENT, 'STUDENT', APPOINTMENT)?.phase).toBe('submitting');

    await act(async () => {
      proposalIntents.bindAccount(OTHER, 'STUDENT');
      mock.userId = OTHER;
      mock.services.proposalHistory.mockResolvedValue(history());
      view.rerender(
        <ProposalPanel
          appointment={appointment}
          role="STUDENT"
          timezone="Asia/Ho_Chi_Minh"
          onRefresh={view.onRefresh}
        />,
      );
    });
    expect(oldSignal.aborted).toBe(true);
    expect(screen.queryByLabelText('Phục hồi thao tác đổi lịch')).not.toBeInTheDocument();
    await act(async () => resolveDecision({ id: PENDING, status: 'ACCEPTED' }));
    expect(await screen.findByText('Hiện không có đề xuất đang chờ phản hồi.')).toBeVisible();
    expect(view.onRefresh).toHaveBeenCalledTimes(1);
    proposalIntents.bindAccount(STUDENT, 'STUDENT');
    expect(proposalIntents.get(STUDENT, 'STUDENT', APPOINTMENT)?.phase).toBe('unknown');
  });

  it('does not render a late history response from a previous authenticated account', async () => {
    asStudent();
    let finishRead!: (value: ProposalHistoryPage) => void;
    mock.services.proposalHistory.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRead = resolve;
        }),
    );
    const view = panel();
    await waitFor(() => expect(mock.services.proposalHistory).toHaveBeenCalledTimes(1));
    await act(async () => {
      proposalIntents.bindAccount(OTHER, 'STUDENT');
      mock.userId = OTHER;
      mock.services.proposalHistory.mockResolvedValue(history());
      view.rerender(
        <ProposalPanel
          appointment={appointment}
          role="STUDENT"
          timezone="Asia/Ho_Chi_Minh"
          onRefresh={view.onRefresh}
        />,
      );
    });
    expect(await screen.findByText('Hiện không có đề xuất đang chờ phản hồi.')).toBeVisible();
    await act(async () => finishRead(withPending()));
    expect(screen.queryByText('Phòng đề xuất')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Chấp nhận đề xuất' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Phục hồi thao tác đổi lịch')).not.toBeInTheDocument();
  });

  it('blocks new intentions when recovery data is malformed and asks for review before discarding it', async () => {
    const storageKey = `prl:proposal-intent:v1:STAFF:${STAFF}:${APPOINTMENT}`;
    window.sessionStorage.setItem(storageKey, '{damaged');
    panel();
    const controls = userEvent.setup();
    expect(await screen.findByRole('button', { name: 'Đề xuất đổi lịch' })).toBeDisabled();
    expect(mock.services.createProposal).not.toHaveBeenCalled();
    await controls.click(screen.getByRole('button', { name: 'Xem cách khôi phục bộ nhớ' }));
    const dialog = screen.getByRole('dialog', { name: 'Đối chiếu dữ liệu phục hồi' });
    expect(within(dialog).getByText(/không gửi yêu cầu đổi lịch/)).toBeVisible();
    expect(window.sessionStorage.getItem(storageKey)).toBe('{damaged');
    await controls.click(within(dialog).getByRole('button', { name: 'Tôi đã đối chiếu lịch sử' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Đề xuất đổi lịch' })).toBeEnabled(),
    );
    expect(window.sessionStorage.getItem(storageKey)).toBeNull();
    expect(mock.services.createProposal).not.toHaveBeenCalled();
  });

  it('refreshes worker terminal state while the page is open and removes decision actions', async () => {
    asStudent();
    mock.services.proposalHistory.mockResolvedValue(withPending());
    const { onRefresh } = panel();
    await screen.findByRole('button', { name: 'Chấp nhận đề xuất' });
    mock.services.proposalHistory.mockResolvedValue(
      history({
        items: [{ ...pending, status: 'EXPIRED', resolvedAt: '2026-10-27T17:00:01Z' }],
        total: 1,
        context: { ...history().context, proposalVersion: 7, appointmentStatus: 'CANCELLED' },
      }),
    );
    fireEvent(window, new Event('focus'));
    expect(await screen.findByText('Đã hết hạn')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Chấp nhận đề xuất' })).not.toBeInTheDocument();
    expect(onRefresh).toHaveBeenCalled();
  });
});
