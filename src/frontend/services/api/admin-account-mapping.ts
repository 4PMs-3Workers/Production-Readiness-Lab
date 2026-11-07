import type { AdminAccount } from '@/services/admin-contracts';
import type { Page } from '@/types/api';
import { ApiError } from './errors';

function requireAccount(condition: unknown): asserts condition {
  if (!condition) throw new ApiError('SERVICE_UNAVAILABLE');
}
const uuid = /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
const timestamp = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/;

export function parseAdminAccount(value: unknown): AdminAccount {
  requireAccount(value && typeof value === 'object');
  const data = value as Record<string, unknown>;
  requireAccount(
    typeof data.id === 'string' &&
      uuid.test(data.id) &&
      typeof data.email === 'string' &&
      typeof data.name === 'string' &&
      typeof data.role === 'string' &&
      ['STUDENT', 'STAFF', 'ADMIN'].includes(data.role) &&
      typeof data.is_active === 'boolean' &&
      [data.student_code, data.faculty, data.major].every(
        (v) => v === null || typeof v === 'string',
      ) &&
      (data.gender === null ||
        (typeof data.gender === 'string' &&
          ['MALE', 'FEMALE', 'OTHER', 'UNDISCLOSED'].includes(data.gender))) &&
      [data.created_at, data.updated_at].every(
        (v) => typeof v === 'string' && timestamp.test(v) && Number.isFinite(Date.parse(v)),
      ),
  );
  // Pick response fields explicitly; never copy unexpected secret-bearing fields.
  return {
    id: data.id,
    email: data.email,
    name: data.name,
    role: data.role as AdminAccount['role'],
    is_active: data.is_active,
    student_code: data.student_code as string | null,
    faculty: data.faculty as string | null,
    major: data.major as string | null,
    gender: data.gender as AdminAccount['gender'],
    created_at: data.created_at as string,
    updated_at: data.updated_at as string,
  };
}
export function parseAdminAccountPage(value: unknown): Page<AdminAccount> {
  requireAccount(value && typeof value === 'object');
  const data = value as Record<string, unknown>;
  requireAccount(
    Array.isArray(data.items) &&
      Number.isInteger(data.page) &&
      Number(data.page) > 0 &&
      Number.isInteger(data.page_size) &&
      Number(data.page_size) > 0 &&
      Number(data.page_size) <= 100 &&
      Number.isInteger(data.total) &&
      Number(data.total) >= 0,
  );
  return {
    items: data.items.map(parseAdminAccount),
    page: Number(data.page),
    pageSize: Number(data.page_size),
    total: Number(data.total),
  };
}
