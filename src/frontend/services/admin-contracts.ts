import type { ServiceRequestOptions } from './contracts';
import type { SessionUser } from '@/types/api';

export type AccountRole = SessionUser['role'];
export type AccountGender = 'MALE' | 'FEMALE' | 'OTHER' | 'UNDISCLOSED';

export interface AccountFields {
  readonly email: string;
  readonly name: string;
  readonly role: AccountRole;
  readonly is_active: boolean;
  readonly student_code: string | null;
  readonly faculty: string | null;
  readonly major: string | null;
  readonly gender: AccountGender | null;
}
export interface AdminAccount extends AccountFields {
  readonly id: string;
  readonly created_at: string;
  /** Opaque precondition: preserve all fractional digits when sending it back. */
  readonly updated_at: string;
}
export interface AdminAccountQuery {
  readonly page: number;
  readonly pageSize?: number;
  readonly role?: AccountRole;
  readonly isActive?: boolean;
  readonly search?: string;
}
export interface AccountCreateBody extends AccountFields {
  readonly password: string;
}
export type AccountUpdateBody = Partial<AccountFields> & {
  readonly expected_updated_at: string;
};
export interface AccountPasswordBody {
  readonly expected_updated_at: string;
  readonly password: string;
}
export interface AdminAccessOptions extends ServiceRequestOptions {
  readonly expectedUserId: string;
  readonly expectedSessionGeneration: string;
}
export interface AdminWriteOptions extends AdminAccessOptions {
  readonly onDispatch?: () => void;
}
