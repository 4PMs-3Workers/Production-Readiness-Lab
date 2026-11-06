import type { AdminAccount } from '@/services/admin-contracts';

export const adminActor = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Quản trị mẫu',
  email: 'admin@example.invalid',
  role: 'ADMIN' as const,
};
export const accountFixture: AdminAccount = {
  id: '22222222-2222-4222-8222-222222222222',
  name: 'Tài khoản mẫu',
  email: 'target@example.invalid',
  role: 'STUDENT',
  is_active: true,
  student_code: 'TEST',
  faculty: null,
  major: null,
  gender: null,
  created_at: '2026-11-01T01:00:00.123456Z',
  updated_at: '2026-11-05T01:00:00.123456Z',
};
export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
