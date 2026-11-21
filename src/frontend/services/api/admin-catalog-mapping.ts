import type { AdminPurpose, PurposeValues } from '@/services/admin-catalog-contracts';
import type { Page } from '@/types/api';
import {
  adminRecord,
  positiveSmallInt,
  requireAdminContract,
  revisionTimestamp,
} from './admin-configuration-mapping';

export const purposeCodePattern = /^[A-Z][A-Z0-9_]{0,49}$/;
const uuid = /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
export function purposeMatches(a: PurposeValues, b: PurposeValues) {
  return (
    a.name.trim() === b.name.trim() &&
    a.duration_minutes === b.duration_minutes &&
    a.requires_free_text === b.requires_free_text &&
    a.is_active === b.is_active
  );
}
export function parseAdminPurpose(value: unknown): AdminPurpose {
  const data = adminRecord(value);
  requireAdminContract(
    typeof data.id === 'string' &&
      uuid.test(data.id) &&
      typeof data.code === 'string' &&
      purposeCodePattern.test(data.code) &&
      typeof data.name === 'string' &&
      data.name.trim().length > 0 &&
      Array.from(data.name).length <= 150 &&
      positiveSmallInt(data.duration_minutes) &&
      typeof data.requires_free_text === 'boolean' &&
      typeof data.is_active === 'boolean' &&
      revisionTimestamp(data.updated_at),
  );
  return {
    id: data.id,
    code: data.code,
    name: data.name,
    duration_minutes: data.duration_minutes,
    requires_free_text: data.requires_free_text,
    is_active: data.is_active,
    updated_at: data.updated_at,
  };
}
export function parseAdminPurposePage(value: unknown): Page<AdminPurpose> {
  const data = adminRecord(value);
  requireAdminContract(
    Array.isArray(data.items) &&
      Number.isSafeInteger(data.page) &&
      Number(data.page) >= 1 &&
      Number.isInteger(data.page_size) &&
      Number(data.page_size) >= 1 &&
      Number(data.page_size) <= 100 &&
      Number.isSafeInteger(data.total) &&
      Number(data.total) >= 0,
  );
  requireAdminContract(data.items.length <= Number(data.page_size));
  return {
    items: data.items.map(parseAdminPurpose),
    page: Number(data.page),
    pageSize: Number(data.page_size),
    total: Number(data.total),
  };
}
