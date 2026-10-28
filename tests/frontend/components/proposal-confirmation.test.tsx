import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProposalConfirmation } from '@/features/proposals/proposal-confirmation';

describe('proposal confirmation focus recovery', () => {
  it('restores an enabled control after busy clears and traps both Tab directions from container focus', async () => {
    const controls = userEvent.setup();
    const props = {
      title: 'Xác nhận đề xuất',
      confirmLabel: 'Gửi đề xuất',
      onConfirm: vi.fn(),
      onClose: vi.fn(),
      children: <p>Nội dung đề xuất</p>,
    };
    const content = (busy: boolean) => (
      <>
        <button>Background</button>
        <ProposalConfirmation {...props} busy={busy} />
      </>
    );
    const view = render(content(false));
    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Gửi đề xuất' });
    const back = within(dialog).getByRole('button', { name: 'Quay lại' });
    expect(confirm).toHaveFocus();
    view.rerender(content(true));
    expect(dialog).toHaveFocus();
    await controls.tab({ shift: true });
    expect(dialog).toHaveFocus();
    await controls.keyboard('{Escape}');
    expect(props.onClose).not.toHaveBeenCalled();
    view.rerender(content(false));
    expect(confirm).toHaveFocus();
    dialog.focus();
    await controls.tab({ shift: true });
    expect(back).toHaveFocus();
    dialog.focus();
    await controls.tab();
    expect(confirm).toHaveFocus();
  });
});
