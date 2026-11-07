import type { AccountFields, AccountUpdateBody, AdminAccount } from '@/services/admin-contracts';

export const EMPTY_ACCOUNT: AccountFields = {
  email: '',
  name: '',
  role: 'STUDENT',
  is_active: true,
  student_code: null,
  faculty: null,
  major: null,
  gender: null,
};
export function normalizedFields(fields: AccountFields): AccountFields {
  return {
    ...fields,
    email: fields.email.trim().toLowerCase(),
    name: fields.name.trim(),
    student_code: fields.role === 'STUDENT' ? fields.student_code?.trim() || null : null,
    faculty: fields.role === 'STUDENT' ? fields.faculty?.trim() || null : null,
    major: fields.role === 'STUDENT' ? fields.major?.trim() || null : null,
    gender: fields.role === 'STUDENT' ? fields.gender : null,
  };
}
export function accountValidation(fields: AccountFields, password?: string): string | null {
  const email = fields.email.trim();
  const [local, domain, extra] = email.split('@');
  if (
    !local ||
    !domain?.includes('.') ||
    extra !== undefined ||
    /\s/.test(email) ||
    Array.from(email).length > 254
  )
    return 'Email chưa hợp lệ.';
  if (!fields.name.trim() || Array.from(fields.name).length > 150)
    return 'Tên cần có từ 1 đến 150 ký tự.';
  if (
    password !== undefined &&
    (Array.from(password).length < 1 || Array.from(password).length > 1024)
  )
    return 'Mật khẩu cần có từ 1 đến 1024 ký tự.';
  if (
    fields.role === 'STUDENT' &&
    (Array.from(fields.student_code ?? '').length > 50 ||
      Array.from(fields.faculty ?? '').length > 150 ||
      Array.from(fields.major ?? '').length > 150)
  )
    return 'Thông tin sinh viên vượt quá độ dài cho phép.';
  return null;
}
export function accountChanges(
  account: AdminAccount,
  fields: AccountFields,
): AccountUpdateBody | null {
  const normalized = normalizedFields(fields);
  const changed: Partial<AccountFields> = Object.fromEntries(
    Object.entries(normalized).filter(
      ([key, value]) => value !== account[key as keyof AccountFields],
    ),
  );
  if (!Object.keys(changed).length) return null;
  return { ...changed, expected_updated_at: account.updated_at };
}
