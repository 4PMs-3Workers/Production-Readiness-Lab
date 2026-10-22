import { NotificationInbox } from '@/features/notifications/notification-inbox';
import { isMockMode } from '@/lib/application-mode';

export default function NotificationsPage() {
  if (isMockMode())
    return (
      <section>
        <h1>Thông báo chưa có trong bản demo</h1>
        <p>Chuyển sang chế độ API để xem thông báo cá nhân.</p>
      </section>
    );
  return <NotificationInbox />;
}
