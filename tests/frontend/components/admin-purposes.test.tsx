import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiServices } from '@/services/api/client';
import { AdminWorkspace } from '@/features/admin/admin-workspace';
import { AdminRecovery } from '@/features/admin/admin-recovery';
import { AdminPurposes } from '@/features/admin/purposes/admin-purposes';
import { adminActor as actor, deferred } from '../admin-fixtures';
import { purposeFixture } from '../admin-configuration-fixtures';

const context = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('@/features/api/api-provider', () => ({ useApi: () => context.value }));
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
let purpose = structuredClone(purposeFixture);
beforeEach(() => {
  purpose = structuredClone(purposeFixture);
  fetcher = vi.fn<typeof fetch>(async (path, init) => {
    if (String(path).endsWith('/auth/me')) return Response.json(actor);
    if (String(path).endsWith('/auth/csrf'))
      return Response.json({
        user_id: actor.id,
        session_generation: actor.session_generation,
        csrf_token: 'csrf',
      });
    if (init?.method) {
      purpose = {
        ...purpose,
        ...JSON.parse(String(init.body)),
        updated_at: '2026-11-09T01:00:00.654321Z',
      };
      return Response.json(purpose, { status: init.method === 'POST' ? 201 : 200 });
    }
    return Response.json(
      String(path).includes('?') ? { items: [purpose], page: 1, page_size: 20, total: 1 } : purpose,
    );
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
  };
});
function Harness() {
  return (
    <AdminWorkspace>
      <AdminRecovery />
      <AdminPurposes />
    </AdminWorkspace>
  );
}
const writes = () =>
  fetcher.mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT');
async function edit() {
  await userEvent.click(await screen.findByRole('button', { name: 'Sửa ADVICE' }));
  await screen.findByLabelText('Tên nhu cầu');
}
async function confirm() {
  await userEvent.click(screen.getByRole('button', { name: 'Xem lại nhu cầu' }));
  await userEvent.click(screen.getByRole('button', { name: 'Xác nhận lưu nhu cầu' }));
}

describe('ADMIN purpose screen', () => {
  it('validates new fields and never dispatches invalid input', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Tạo nhu cầu' }));
    await userEvent.click(screen.getByRole('button', { name: 'Xem lại nhu cầu' }));
    expect(screen.getByText(/Mã gồm 1–50/)).toBeVisible();
    expect(screen.getByText('Nhập tên từ 1 đến 150 ký tự.')).toBeVisible();
    expect(writes()).toHaveLength(0);
  });
  it('keeps code immutable and confirms deactivation without a DELETE', async () => {
    render(<Harness />);
    await edit();
    expect(screen.getByLabelText('Mã nhu cầu')).toHaveAttribute('readonly');
    await userEvent.click(screen.getByLabelText('Hoạt động', { exact: true }));
    await userEvent.click(screen.getByRole('button', { name: 'Xem lại nhu cầu' }));
    expect(screen.getByText(/Mã, liên kết nhân viên và lịch sử được giữ nguyên/)).toBeVisible();
    expect(writes()).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Xác nhận lưu nhu cầu' }));
    await screen.findByText('Backend đã xác nhận trạng thái yêu cầu.');
    expect(writes()[0][1]?.method).toBe('PUT');
    expect(JSON.parse(String(writes()[0][1]?.body))).not.toHaveProperty('code');
    expect(JSON.parse(String(writes()[0][1]?.body))).toMatchObject({
      is_active: false,
      expected_updated_at: purposeFixture.updated_at,
    });
  });
  it('recognizes local no-op and cancels review without writing', async () => {
    render(<Harness />);
    await edit();
    await userEvent.click(screen.getByRole('button', { name: 'Xem lại nhu cầu' }));
    expect(screen.getByText('Không có thay đổi.')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Tên nhu cầu'), { target: { value: 'Mới' } });
    await userEvent.click(screen.getByRole('button', { name: 'Xem lại nhu cầu' }));
    await userEvent.click(screen.getByRole('button', { name: 'Quay lại chỉnh sửa' }));
    expect(writes()).toHaveLength(0);
  });
  it('keeps backend validation errors editable and never claims success', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method
        ? Promise.resolve(
            Response.json(
              { error: { code: 'VALIDATION_ERROR' } },
              { status: 422, headers: { 'X-Request-ID': 'safe-reference' } },
            ),
          )
        : original(path, init),
    );
    render(<Harness />);
    await edit();
    fireEvent.change(screen.getByLabelText('Thời lượng (phút)'), { target: { value: '17' } });
    await confirm();
    await screen.findByText(/Giá trị hoặc các khoảng giờ chưa hợp lệ/);
    expect(screen.getByLabelText('Thời lượng (phút)')).toBeEnabled();
    expect(screen.queryByText('Backend đã xác nhận trạng thái yêu cầu.')).not.toBeInTheDocument();
  });
  it('does not overwrite the editor with late list refreshes', async () => {
    render(<Harness />);
    await edit();
    fireEvent.change(screen.getByLabelText('Tên nhu cầu'), {
      target: { value: 'Draft còn nguyên' },
    });
    purpose = { ...purpose, name: 'Sửa ở nơi khác', updated_at: '2026-11-09T03:00:00.000001Z' };
    await userEvent.click(screen.getByRole('button', { name: 'Tải lại danh mục' }));
    await screen.findByRole('heading', { name: 'Sửa ở nơi khác' });
    expect(screen.getByLabelText('Tên nhu cầu')).toHaveValue('Draft còn nguyên');
    await confirm();
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(JSON.parse(String(writes()[0][1]?.body)).expected_updated_at).toBe(
      purposeFixture.updated_at,
    );
  });
  it('recovers an unknown create by deliberately replaying the same immutable code and full body', async () => {
    const original = fetcher.getMockImplementation()!;
    let attempts = 0;
    fetcher.mockImplementation(async (path, init) => {
      const result = await original(path, init);
      if (init?.method === 'POST' && ++attempts === 1) throw new TypeError('lost after commit');
      return result;
    });
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Tạo nhu cầu' }));
    fireEvent.change(screen.getByLabelText('Mã nhu cầu'), { target: { value: 'NEW_ADVICE' } });
    fireEvent.change(screen.getByLabelText('Tên nhu cầu'), { target: { value: 'Tư vấn mới' } });
    await confirm();
    await screen.findByRole('heading', { name: 'Chưa xác định kết quả lưu' });
    expect(screen.getByRole('button', { name: 'Tạo nhu cầu' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Gửi lại đúng yêu cầu cũ' }));
    await screen.findByText('Backend đã xác nhận trạng thái yêu cầu.');
    expect(writes()).toHaveLength(2);
    expect(writes()[0][1]?.body).toBe(writes()[1][1]?.body);
  });
  it('does not confirm a malformed successful receipt and holds a new intent until review', async () => {
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method
        ? Promise.resolve(Response.json({ ...purpose, is_active: 'false' }))
        : original(path, init),
    );
    render(<Harness />);
    await edit();
    fireEvent.change(screen.getByLabelText('Tên nhu cầu'), { target: { value: 'Edited' } });
    await confirm();
    await screen.findByRole('heading', { name: 'Chưa xác định kết quả lưu' });
    expect(writes()).toHaveLength(1);
  });
  it('prevents repeated dispatch while awaiting a create receipt', async () => {
    const held = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((path, init) =>
      init?.method ? held.promise : original(path, init),
    );
    render(<Harness />);
    await edit();
    fireEvent.change(screen.getByLabelText('Tên nhu cầu'), { target: { value: 'Held' } });
    await userEvent.click(screen.getByRole('button', { name: 'Xem lại nhu cầu' }));
    const button = screen.getByRole('button', { name: 'Xác nhận lưu nhu cầu' });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(writes()).toHaveLength(1));
    await act(async () => held.resolve(Response.json({ ...purpose, name: 'Held' })));
    await screen.findByText('Backend đã xác nhận trạng thái yêu cầu.');
  });
});
