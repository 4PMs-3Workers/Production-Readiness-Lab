import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DemoProvider } from '@/features/demo/demo-provider';
import { LoginFlow } from '@/features/login/login-flow';
import { createMockServices } from '@/services';
import type { StudentContext } from '@/types/booking';

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

beforeEach(() => router.replace.mockClear());

describe('demo login', () => {
  it('validates fields, focuses the first invalid input, and submits only valid data', async () => {
    const user = userEvent.setup();
    const services = createMockServices({ delayMs: 0 });
    const login = vi.spyOn(services, 'loginDemo');
    render(
      <DemoProvider services={services}>
        <LoginFlow />
      </DemoProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Đăng nhập demo' }));
    expect(screen.getByLabelText('Email')).toHaveFocus();
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true');
    expect(login).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('Email'), 'student@example.test');
    await user.click(screen.getByRole('button', { name: 'Đăng nhập demo' }));
    expect(screen.getByLabelText('Mật khẩu')).toHaveFocus();
    await user.type(screen.getByLabelText('Mật khẩu'), '  demo  ');
    await user.click(screen.getByRole('button', { name: 'Đăng nhập demo' }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/home'));
    expect(login).toHaveBeenCalledWith(
      { email: 'student@example.test', password: '  demo  ' },
      { signal: expect.any(AbortSignal) },
    );
    expect(screen.getByRole('link', { name: 'Vào trang chủ' })).toBeVisible();
  });

  it.each([
    ['invalid-login', 'Thông tin đăng nhập demo không hợp lệ.'],
    ['login-error', 'Dịch vụ demo tạm thời không khả dụng. Vui lòng thử lại.'],
  ] as const)(
    'recovers from %s without keeping the submitted password',
    async (scenario, message) => {
      const user = userEvent.setup();
      const services = createMockServices({ scenario, delayMs: 0 });
      render(
        <DemoProvider services={services}>
          <LoginFlow />
        </DemoProvider>,
      );
      await user.type(screen.getByLabelText('Email'), 'student@example.test');
      await user.type(screen.getByLabelText('Mật khẩu'), 'demo');
      await user.click(screen.getByRole('button', { name: 'Đăng nhập demo' }));
      expect(await screen.findByRole('alert')).toHaveTextContent(message);
      expect(screen.getByRole('alert')).toHaveFocus();
      expect(screen.getByLabelText('Email')).toHaveValue('student@example.test');
      expect(screen.getByLabelText('Mật khẩu')).toHaveValue('');
      expect(screen.getByRole('button', { name: 'Đăng nhập demo' })).toBeEnabled();
      expect(router.replace).not.toHaveBeenCalled();
    },
  );

  it('cancels on leave, prevents duplicates, and ignores an old success during a newer login', async () => {
    const user = userEvent.setup();
    const services = createMockServices({ delayMs: 0 });
    const oldRequest = Promise.withResolvers<StudentContext>();
    const newRequest = Promise.withResolvers<StudentContext>();
    const login = vi
      .spyOn(services, 'loginDemo')
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => newRequest.promise);
    const view = render(
      <DemoProvider services={services}>
        <LoginFlow />
      </DemoProvider>,
    );
    async function submit() {
      await user.type(screen.getByLabelText('Email'), 'student@example.test');
      await user.type(screen.getByLabelText('Mật khẩu'), 'demo');
      await user.click(screen.getByRole('button', { name: 'Đăng nhập demo' }));
      fireEvent.submit(screen.getByRole('form', { name: 'Đăng nhập demo' }));
      expect(screen.getByLabelText('Mật khẩu')).toHaveValue('');
      expect(screen.getByRole('button', { name: 'Đang đăng nhập…' })).toBeDisabled();
    }
    await submit();
    expect(login).toHaveBeenCalledTimes(1);
    const oldSignal = login.mock.calls[0][1]?.signal;
    view.rerender(
      <DemoProvider services={services}>
        <p>Another page</p>
      </DemoProvider>,
    );
    expect(oldSignal?.aborted).toBe(true);
    view.rerender(
      <DemoProvider services={services}>
        <LoginFlow />
      </DemoProvider>,
    );
    await submit();
    expect(login).toHaveBeenCalledTimes(2);
    await act(async () => oldRequest.resolve(services.getDemoStudent()));
    expect(router.replace).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Đang đăng nhập…' })).toBeDisabled();
    await act(async () => newRequest.resolve(services.getDemoStudent()));
    expect(router.replace).toHaveBeenCalledExactlyOnceWith('/home');
    expect(screen.getByRole('link', { name: 'Vào trang chủ' })).toBeVisible();
  });
});
