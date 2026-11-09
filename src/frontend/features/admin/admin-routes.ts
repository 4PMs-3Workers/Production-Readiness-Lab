export const adminSections = {
  accounts: { path: '/admin/accounts', title: 'Quản lý tài khoản' },
  purposes: { path: '/admin/purposes', title: 'Danh mục tư vấn' },
  'service-hours': { path: '/admin/service-hours', title: 'Giờ phục vụ' },
  'booking-settings': { path: '/admin/booking-settings', title: 'Quy định đặt lịch' },
} as const;
export type AdminSection = keyof typeof adminSections;
export function adminSectionFor(path: string): AdminSection {
  return (
    (Object.keys(adminSections) as AdminSection[]).find(
      (section) => adminSections[section].path === path,
    ) ?? 'accounts'
  );
}
