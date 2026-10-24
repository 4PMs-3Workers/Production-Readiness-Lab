import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiHistory, ApiResult } from '@/features/api/api-appointments';
import { ApiProvider } from '@/features/api/api-provider';
import { ApiServices } from '@/services/api/client';

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/features/proposals/proposal-panel', () => ({ ProposalPanel: () => null }));

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  name: 'Fictional Student',
  email: 'student@example.invalid',
  role: 'STUDENT',
};
const context = {
  timezone: 'Asia/Ho_Chi_Minh',
  business_date: '2026-10-19',
  min_booking_date: '2026-10-20',
  max_booking_date: '2026-10-26',
};
const appointment = {
  id: 'appointment-a',
  purpose_id: 'purpose-a',
  purpose_code: 'HISTORY',
  purpose_name: 'Lịch sử tư vấn',
  staff_id: 'staff-a',
  staff_name: 'Cố vấn kiểm thử',
  duration_minutes: 30,
  starts_at: '2026-10-18T18:00:00Z',
  ends_at: '2026-10-18T18:30:00Z',
  created_at: '2026-10-17T02:00:00Z',
  location: null,
  status: 'CANCELLED',
  description: null,
  cancelled_at: null,
  cancel_reason: null,
};
const json = (value: unknown, status = 200) => Response.json(value, { status });
const failure = (code: string, status: number) => json({ error: { code } }, status);

function page(items = [appointment]) {
  return { items, page: 1, page_size: 20, total: items.length };
}

function renderHistory(fetcher: typeof fetch) {
  return render(
    <ApiProvider services={new ApiServices(fetcher)}>
      <ApiHistory />
    </ApiProvider>,
  );
}

function renderDetail(fetcher: typeof fetch) {
  return render(
    <ApiProvider services={new ApiServices(fetcher)}>
      <ApiResult bookingId="appointment-a" />
    </ApiProvider>,
  );
}

describe('saved appointments and service timezone recovery', () => {
  it('keeps history visible in explicitly labeled UTC when context is unavailable, then refreshes only timezone', async () => {
    let contextAttempts = 0;
    const fetcher = vi.fn<typeof fetch>(async (path) => {
      const url = String(path);
      if (url.endsWith('/auth/me')) return json(user);
      if (url.endsWith('/booking-context'))
        return ++contextAttempts === 1 ? failure('SERVICE_UNAVAILABLE', 503) : json(context);
      if (url.includes('/appointments?')) return json(page());
      throw new Error(`Unexpected request: ${url}`);
    });
    const controls = userEvent.setup();
    renderHistory(fetcher);

    expect(
      await screen.findByText('Đang hiển thị giờ UTC vì chưa lấy được múi giờ dịch vụ.'),
    ).toBeVisible();
    expect(screen.getByText('18:00 – 18:30 · 30 phút')).toBeVisible();
    expect(screen.getByText(/1 lịch hẹn.*UTC/)).toBeVisible();

    await controls.click(screen.getByRole('button', { name: 'Thử lại múi giờ' }));

    expect(await screen.findByText('19/10/2026')).toBeVisible();
    expect(screen.getByText('01:00 – 01:30 · 30 phút')).toBeVisible();
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).includes('/appointments?')),
    ).toHaveLength(1);
  });

  it('keeps appointment detail visible through a context outage and formats with the recovered timezone', async () => {
    let contextAttempts = 0;
    const fetcher = vi.fn<typeof fetch>(async (path) => {
      const url = String(path);
      if (url.endsWith('/auth/me')) return json(user);
      if (url.endsWith('/booking-context'))
        return ++contextAttempts === 1 ? failure('SERVICE_UNAVAILABLE', 503) : json(context);
      if (url.endsWith('/appointments/appointment-a')) return json(appointment);
      throw new Error(`Unexpected request: ${url}`);
    });
    const controls = userEvent.setup();
    renderDetail(fetcher);

    expect(await screen.findByRole('heading', { name: 'Chi tiết lịch hẹn' })).toBeVisible();
    expect(
      screen.getByText('Đang hiển thị giờ UTC vì chưa lấy được múi giờ dịch vụ.'),
    ).toBeVisible();
    expect(screen.getByText('18:00 – 18:30')).toBeVisible();

    await controls.click(screen.getByRole('button', { name: 'Thử lại múi giờ' }));

    expect(await screen.findByText('19/10/2026')).toBeVisible();
    expect(screen.getByText('01:00 – 01:30')).toBeVisible();
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).endsWith('/appointments/appointment-a')),
    ).toHaveLength(1);
  });

  it.each([
    ['401', 'INVALID_SESSION', 401],
    ['403', 'FORBIDDEN_ROLE', 403],
  ])(
    'keeps a context %s visible as an error instead of labeling UTC as a fallback',
    async (_label, code, status) => {
      const fetcher = vi.fn<typeof fetch>(async (path) => {
        const url = String(path);
        if (url.endsWith('/auth/me')) return json(user);
        if (url.endsWith('/booking-context')) return failure(code, status);
        if (url.includes('/appointments?')) return json(page());
        throw new Error(`Unexpected request: ${url}`);
      });
      renderHistory(fetcher);

      expect(await screen.findByText('Lịch sử tư vấn')).toBeVisible();
      await waitFor(() => expect(screen.getByRole('alert')).toBeVisible());
      expect(
        screen.queryByText('Đang hiển thị giờ UTC vì chưa lấy được múi giờ dịch vụ.'),
      ).not.toBeInTheDocument();
    },
  );

  it.each([
    ['401', 'INVALID_SESSION', 401],
    ['403', 'FORBIDDEN_ROLE', 403],
  ])(
    'keeps a detail context %s visible as an error instead of UTC fallback',
    async (_label, code, status) => {
      const fetcher = vi.fn<typeof fetch>(async (path) => {
        const url = String(path);
        if (url.endsWith('/auth/me')) return json(user);
        if (url.endsWith('/booking-context')) return failure(code, status);
        if (url.endsWith('/appointments/appointment-a')) return json(appointment);
        throw new Error(`Unexpected request: ${url}`);
      });
      renderDetail(fetcher);

      await waitFor(() => expect(screen.getByRole('alert')).toBeVisible());
      expect(
        screen.queryByText('Đang hiển thị giờ UTC vì chưa lấy được múi giờ dịch vụ.'),
      ).not.toBeInTheDocument();
    },
  );
});
