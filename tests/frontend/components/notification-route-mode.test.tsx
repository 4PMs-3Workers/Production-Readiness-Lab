import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import NotificationsLayout from '@/app/notifications/layout';
import NotificationsPage from '@/app/notifications/page';
import StaffNotificationsPage from '@/app/staff/notifications/page';
import { ApplicationProvider } from '@/components/application-provider';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => '/notifications',
  useSearchParams: () => new URLSearchParams(),
}));

describe('notification routes in explicit mock mode', () => {
  it('shows an unavailable notice in the student shell without an API session', () => {
    render(
      <ApplicationProvider>
        <NotificationsLayout>
          <NotificationsPage />
        </NotificationsLayout>
      </ApplicationProvider>,
    );

    expect(screen.getByRole('heading', { name: 'Thông báo chưa có trong bản demo' })).toBeVisible();
  });

  it('does not require an API session to render the staff page', () => {
    render(
      <ApplicationProvider>
        <StaffNotificationsPage />
      </ApplicationProvider>,
    );

    expect(screen.getByRole('heading', { name: 'Thông báo chưa có trong bản demo' })).toBeVisible();
  });
});
