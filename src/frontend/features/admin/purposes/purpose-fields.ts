import type { AdminPurpose, PurposeCreateBody } from '@/services/admin-catalog-contracts';
import { positiveSmallInt } from '@/services/api/admin-configuration-mapping';
import { purposeCodePattern } from '@/services/api/admin-catalog-mapping';

export interface PurposeDraft {
  code: string;
  name: string;
  duration_minutes: string;
  requires_free_text: boolean;
  is_active: boolean;
}
export function purposeDraft(value: AdminPurpose | null): PurposeDraft {
  return value
    ? {
        code: value.code,
        name: value.name,
        duration_minutes: String(value.duration_minutes),
        requires_free_text: value.requires_free_text,
        is_active: value.is_active,
      }
    : { code: '', name: '', duration_minutes: '30', requires_free_text: false, is_active: true };
}
export function purposeErrors(value: PurposeDraft) {
  const errors: Record<string, string> = {};
  if (!purposeCodePattern.test(value.code))
    errors.code = 'Mã gồm 1–50 chữ cái in hoa, số hoặc dấu gạch dưới; bắt đầu bằng chữ cái.';
  if (!value.name.trim() || Array.from(value.name.trim()).length > 150)
    errors.name = 'Nhập tên từ 1 đến 150 ký tự.';
  if (!value.duration_minutes.trim() || !positiveSmallInt(Number(value.duration_minutes)))
    errors.duration_minutes = 'Nhập số phút nguyên từ 1 đến 32767.';
  return errors;
}
export function purposeBody(value: PurposeDraft): PurposeCreateBody {
  return {
    code: value.code,
    name: value.name.trim(),
    duration_minutes: Number(value.duration_minutes),
    requires_free_text: value.requires_free_text,
    is_active: value.is_active,
  };
}
