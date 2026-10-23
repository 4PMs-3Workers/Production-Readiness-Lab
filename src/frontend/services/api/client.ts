import type {
  AvailableSlotsQuery,
  BookingRequestOptions,
  LoginCredentials,
  ServiceRequestOptions,
} from '../contracts';
import type { AvailableSlot, Purpose, Staff } from '@/types/booking';
import type {
  Appointment,
  AppointmentDetail,
  AppointmentStatus,
  BookingBody,
  BookingContext,
  BookingReceipt,
  Page,
  SessionUser,
  StudentProfile,
} from '@/types/api';
import type {
  StaffAppointment,
  StaffAppointmentDetail,
  StaffAppointmentQuery,
  StaffContext,
  StaffOutcomeStatus,
  StaffPage,
  StaffPurpose,
  StaffUnavailability,
  StaffUnavailabilityQuery,
} from '@/services/staff-contracts';
import type {
  NotificationItem,
  NotificationPage,
  NotificationQuery,
} from '@/services/notification-contracts';
import { isBusinessDate } from '@/lib/business-time';
import { ApiError, BookingRejection } from './errors';
import { parseBookingContext } from './booking-context';

interface StaffWriteOptions extends ServiceRequestOptions {
  readonly expectedUserId: string;
}

interface NotificationAccessOptions extends ServiceRequestOptions {
  readonly expectedUserId?: string;
  readonly expectedRole?: 'STUDENT' | 'STAFF';
}

interface WirePage<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}
interface WirePurpose {
  id: string;
  code: string;
  name: string;
  duration_minutes: number;
  requires_free_text: boolean;
}
interface WireUser {
  id: string;
  name: string;
  email: string;
  role: SessionUser['role'];
}
interface WireStudentProfile extends WireUser {
  student_code: string | null;
  faculty: string | null;
  major: string | null;
  gender: StudentProfile['gender'];
}
interface WireAppointment {
  id: string;
  purpose_id: string;
  purpose_code: string;
  purpose_name: string;
  staff_id: string;
  staff_name: string;
  duration_minutes: number;
  starts_at: string;
  ends_at: string;
  location: string | null;
  status: AppointmentStatus;
  created_at: string;
}
interface WireDetail extends WireAppointment {
  description: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
}
interface WireStaffAppointment {
  id: string;
  purpose_id: string;
  purpose_code: string;
  purpose_name: string;
  student_id: string;
  student_name: string;
  duration_minutes: number;
  starts_at: string;
  ends_at: string;
  location: string | null;
  status: StaffAppointment['status'];
  created_at: string;
  cancelled_at: string | null;
  outcome_at: string | null;
}
interface WireStaffAppointmentDetail extends WireStaffAppointment {
  description: string | null;
  cancelled_by: string | null;
  cancel_reason: string | null;
  outcome_by: string | null;
}
interface WireStaffPurpose extends WirePurpose {
  is_active: boolean;
  is_accepting: boolean;
}
interface WireStaffUnavailability {
  id: string;
  starts_at: string;
  ends_at: string;
  created_at: string;
}
interface WireNotification {
  id: string;
  event_type: string;
  summary: string;
  created_at: string;
  read_at: string | null;
  appointment_id: string | null;
  appointment_starts_at: string | null;
  appointment_status: AppointmentStatus | null;
}

const STAFF_APPOINTMENT_STATUSES = [
  'UPCOMING',
  'CHANGE_PENDING',
  'CANCELLED',
  'COMPLETED',
  'NO_SHOW',
] as const;

function requireContract(condition: unknown): asserts condition {
  if (!condition) throw new ApiError('SERVICE_UNAVAILABLE');
}

function mapPage<T, U>(data: WirePage<T>, convert: (item: T) => U): Page<U> {
  requireContract(
    Array.isArray(data.items) &&
      Number.isInteger(data.page) &&
      data.page > 0 &&
      Number.isInteger(data.page_size) &&
      data.page_size > 0 &&
      Number.isInteger(data.total) &&
      data.total >= 0,
  );
  return {
    items: data.items.map(convert),
    page: data.page,
    pageSize: data.page_size,
    total: data.total,
  };
}
function purpose(data: WirePurpose): Purpose {
  return {
    id: data.id,
    code: data.code,
    name: data.name,
    durationMinutes: data.duration_minutes,
    requiresFreeText: data.requires_free_text,
  };
}
function user(data: WireUser): SessionUser {
  requireContract(
    data &&
      typeof data.id === 'string' &&
      data.id.length > 0 &&
      typeof data.name === 'string' &&
      typeof data.email === 'string' &&
      ['STUDENT', 'STAFF', 'ADMIN'].includes(data.role),
  );
  return { id: data.id, displayName: data.name, email: data.email, role: data.role };
}
function appointment(data: WireAppointment): Appointment {
  return {
    id: data.id,
    purpose: {
      id: data.purpose_id,
      code: data.purpose_code,
      name: data.purpose_name,
      durationMinutes: data.duration_minutes,
      requiresFreeText: false,
    },
    staff: { id: data.staff_id, displayName: data.staff_name },
    startsAt: data.starts_at,
    endsAt: data.ends_at,
    location: data.location,
    status: data.status,
    createdAt: data.created_at,
  };
}
function staffAppointment(data: WireStaffAppointment): StaffAppointment {
  requireContract(
    data &&
      typeof data.id === 'string' &&
      typeof data.purpose_id === 'string' &&
      typeof data.purpose_code === 'string' &&
      typeof data.purpose_name === 'string' &&
      typeof data.student_id === 'string' &&
      typeof data.student_name === 'string' &&
      Number.isInteger(data.duration_minutes) &&
      typeof data.starts_at === 'string' &&
      typeof data.ends_at === 'string' &&
      (data.location === null || typeof data.location === 'string') &&
      STAFF_APPOINTMENT_STATUSES.includes(data.status) &&
      typeof data.created_at === 'string',
  );
  return {
    id: data.id,
    purposeId: data.purpose_id,
    purposeCode: data.purpose_code,
    purposeName: data.purpose_name,
    studentId: data.student_id,
    studentName: data.student_name,
    durationMinutes: data.duration_minutes,
    startsAt: data.starts_at,
    endsAt: data.ends_at,
    location: data.location,
    status: data.status,
    createdAt: data.created_at,
    cancelledAt: data.cancelled_at,
    outcomeAt: data.outcome_at,
  };
}
function staffPurpose(data: WireStaffPurpose): StaffPurpose {
  requireContract(
    data &&
      typeof data.id === 'string' &&
      typeof data.code === 'string' &&
      typeof data.name === 'string' &&
      Number.isInteger(data.duration_minutes) &&
      typeof data.requires_free_text === 'boolean' &&
      typeof data.is_active === 'boolean' &&
      typeof data.is_accepting === 'boolean',
  );
  return {
    id: data.id,
    code: data.code,
    name: data.name,
    durationMinutes: data.duration_minutes,
    requiresFreeText: data.requires_free_text,
    isActive: data.is_active,
    isAccepting: data.is_accepting,
  };
}
function staffUnavailability(data: WireStaffUnavailability): StaffUnavailability {
  requireContract(
    data &&
      typeof data.id === 'string' &&
      typeof data.starts_at === 'string' &&
      typeof data.ends_at === 'string' &&
      typeof data.created_at === 'string',
  );
  return {
    id: data.id,
    startsAt: data.starts_at,
    endsAt: data.ends_at,
    createdAt: data.created_at,
  };
}
function notification(data: WireNotification): NotificationItem {
  requireContract(
    data &&
      typeof data.id === 'string' &&
      data.id.length > 0 &&
      typeof data.event_type === 'string' &&
      data.event_type.length > 0 &&
      typeof data.summary === 'string' &&
      typeof data.created_at === 'string' &&
      (data.read_at === null || typeof data.read_at === 'string') &&
      (data.appointment_id === null || typeof data.appointment_id === 'string') &&
      (data.appointment_starts_at === null || typeof data.appointment_starts_at === 'string') &&
      (data.appointment_status === null ||
        STAFF_APPOINTMENT_STATUSES.includes(data.appointment_status)),
  );
  return {
    id: data.id,
    eventType: data.event_type,
    summary: data.summary,
    createdAt: data.created_at,
    readAt: data.read_at,
    appointmentId: data.appointment_id,
    appointmentStartsAt: data.appointment_starts_at,
    appointmentStatus: data.appointment_status,
  };
}

export class ApiServices {
  private scope = new AbortController();
  private onInvalidSession?: () => void;

  setInvalidSessionHandler(handler?: () => void) {
    this.onInvalidSession = handler;
  }
  invalidateSession() {
    this.onInvalidSession?.();
  }

  constructor(
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
    private readonly timeoutMs = 15_000,
  ) {}

  clearSession() {
    this.scope.abort();
    this.scope = new AbortController();
  }

  private async request<T>(
    path: string,
    options: ServiceRequestOptions = {},
    init: RequestInit = {},
    onDispatch?: () => void,
  ): Promise<T> {
    const deadline = new AbortController();
    const scopeSignal = this.scope.signal;
    const signal = AbortSignal.any([
      scopeSignal,
      deadline.signal,
      ...(options.signal ? [options.signal] : []),
    ]);
    const timer = setTimeout(() => deadline.abort(), this.timeoutMs);
    try {
      signal.throwIfAborted();
      onDispatch?.();
      const response = await this.fetcher(`/api/v1${path}`, {
        ...init,
        headers: { Accept: 'application/json', ...init.headers },
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        signal,
      });
      signal.throwIfAborted();
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const code =
          typeof body?.error?.code === 'string' ? body.error.code : 'SERVICE_UNAVAILABLE';
        const requestId = response.headers.get('X-Request-ID') ?? undefined;
        if (code === 'INVALID_SESSION') this.onInvalidSession?.();
        throw new ApiError(
          code,
          response.status,
          requestId,
          response.headers.get('Retry-After') ?? undefined,
        );
      }
      if (response.status === 204) return undefined as T;
      const data = (await response.json()) as T;
      signal.throwIfAborted();
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (options.signal?.aborted || scopeSignal.aborted)
        throw new DOMException('Request cancelled', 'AbortError');
      throw new ApiError(deadline.signal.aborted ? 'TIMEOUT' : 'NETWORK_ERROR');
    } finally {
      clearTimeout(timer);
    }
  }

  private async write<T>(
    path: string,
    body: object | undefined,
    options?: ServiceRequestOptions,
    headers: Record<string, string> = {},
    expectedUserId?: string,
    onDispatch?: () => void,
    method: 'POST' | 'PUT' | 'DELETE' = 'POST',
    expectedRole: 'STUDENT' | 'STAFF' = 'STUDENT',
  ): Promise<T> {
    const signal = AbortSignal.any([
      this.scope.signal,
      ...(options?.signal ? [options.signal] : []),
    ]);
    const scopedOptions = { signal };
    const csrf = await this.request<{ csrf_token: string; user_id: string }>(
      '/auth/csrf',
      scopedOptions,
    );
    requireContract(typeof csrf.csrf_token === 'string' && csrf.csrf_token.length > 0);
    if (expectedUserId !== undefined) {
      // The owner and signed token come from the same authenticated backend context.
      // Keep this exact token: a later cookie switch must fail the server's CSRF check.
      requireContract(typeof csrf.user_id === 'string' && csrf.user_id.length > 0);
      if (csrf.user_id !== expectedUserId) {
        this.onInvalidSession?.();
        throw new ApiError('ACCOUNT_CHANGED', 401);
      }
      const verified = await this.me(scopedOptions);
      if (verified.id !== expectedUserId || verified.role !== expectedRole) {
        this.onInvalidSession?.();
        throw new ApiError('ACCOUNT_CHANGED', 401);
      }
    }
    signal.throwIfAborted();
    return this.request(
      path,
      scopedOptions,
      {
        method,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrf.csrf_token,
          ...headers,
        },
      },
      onDispatch,
    );
  }

  async me(options?: ServiceRequestOptions) {
    return user(await this.request<WireUser>('/auth/me', options));
  }
  async login(credentials: LoginCredentials, options?: ServiceRequestOptions) {
    return user(
      await this.request<WireUser>('/auth/login', options, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      }),
    );
  }
  logout(options?: ServiceRequestOptions) {
    return this.write<void>('/auth/logout', {}, options);
  }
  async context(options?: ServiceRequestOptions): Promise<BookingContext> {
    return parseBookingContext(await this.request<unknown>('/booking-context', options));
  }
  async staffContext(options?: ServiceRequestOptions): Promise<StaffContext> {
    const data = await this.request<{ timezone: string; business_date: string }>(
      '/staff/me/context',
      options,
    );
    try {
      new Intl.DateTimeFormat('en', { timeZone: data.timezone });
    } catch {
      throw new ApiError('SERVICE_UNAVAILABLE');
    }
    requireContract(typeof data.timezone === 'string' && isBusinessDate(data.business_date));
    return { timezone: data.timezone, businessDate: data.business_date };
  }
  async profile(options?: ServiceRequestOptions): Promise<StudentProfile> {
    const data = await this.request<WireStudentProfile>('/students/me', options);
    const identity = user(data);
    requireContract(
      identity.role === 'STUDENT' &&
        [data.student_code, data.faculty, data.major].every(
          (value) => value === null || typeof value === 'string',
        ) &&
        (data.gender === null || ['MALE', 'FEMALE', 'OTHER', 'UNDISCLOSED'].includes(data.gender)),
    );
    return {
      id: identity.id,
      displayName: identity.displayName,
      email: identity.email,
      studentCode: data.student_code,
      faculty: data.faculty,
      major: data.major,
      gender: data.gender,
    };
  }
  async purposes(page = 1, options?: ServiceRequestOptions): Promise<Page<Purpose>> {
    return mapPage(
      await this.request<WirePage<WirePurpose>>(`/purposes?page=${page}&page_size=20`, options),
      purpose,
    );
  }
  async staff(purposeId: string, page = 1, options?: ServiceRequestOptions): Promise<Page<Staff>> {
    return mapPage(
      await this.request<WirePage<{ id: string; name: string }>>(
        `/staff?purpose_id=${encodeURIComponent(purposeId)}&page=${page}&page_size=20`,
        options,
      ),
      (staff) => ({ id: staff.id, displayName: staff.name }),
    );
  }
  async slots(
    query: AvailableSlotsQuery,
    options?: ServiceRequestOptions,
  ): Promise<AvailableSlot[]> {
    const params = new URLSearchParams({
      purpose_id: query.purposeId,
      staff_id: query.staffId,
      date: query.date,
    });
    const data = await this.request<{
      date: string;
      timezone: string;
      slots: { starts_at: string; ends_at: string }[];
    }>(`/availability?${params}`, options);
    return data.slots.map((slot) => ({
      id: `${query.purposeId}:${query.staffId}:${data.date}:${slot.starts_at}:${slot.ends_at}`,
      purposeId: query.purposeId,
      staffId: query.staffId,
      date: data.date,
      startsAt: slot.starts_at,
      endsAt: slot.ends_at,
      location: null,
    }));
  }
  async create(body: BookingBody, key: string, userId: string, options?: BookingRequestOptions) {
    let dispatched = false;
    let receipt: BookingReceipt;
    try {
      receipt = await this.write<BookingReceipt>(
        '/appointments',
        body,
        options,
        { 'Idempotency-Key': key },
        userId,
        () => {
          dispatched = true;
          options?.onDispatch?.();
        },
      );
    } catch (error) {
      // These business conflicts are evaluated after idempotent replay lookup.
      // Auth/bootstrap errors, input validation and transport failures remain ambiguous.
      if (
        dispatched &&
        error instanceof ApiError &&
        error.status === 409 &&
        ['BOOKING_CONFLICT', 'BOOKING_QUOTA_EXCEEDED'].includes(error.code)
      )
        throw new BookingRejection(error.code, error.status, error.requestId, error.retryAfter);
      throw error;
    }
    requireContract(
      receipt &&
        typeof receipt.id === 'string' &&
        receipt.id.length > 0 &&
        receipt.status === 'UPCOMING' &&
        typeof receipt.created_at === 'string',
    );
    return receipt;
  }
  async appointments(
    page = 1,
    status?: AppointmentStatus,
    options?: ServiceRequestOptions,
  ): Promise<Page<Appointment>> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: '20',
      ...(status ? { status } : {}),
    });
    return mapPage(
      await this.request<WirePage<WireAppointment>>(`/appointments?${params}`, options),
      appointment,
    );
  }
  async detail(id: string, options?: ServiceRequestOptions): Promise<AppointmentDetail> {
    const data = await this.request<WireDetail>(`/appointments/${encodeURIComponent(id)}`, options);
    return {
      ...appointment(data),
      description: data.description,
      cancelledAt: data.cancelled_at,
      cancelReason: data.cancel_reason,
    };
  }
  async notificationPage(
    query: NotificationQuery,
    options?: NotificationAccessOptions,
  ): Promise<NotificationPage> {
    await this.verifyNotificationOwner(options);
    const params = new URLSearchParams({
      page: String(query.page),
      page_size: String(query.pageSize),
      unread_only: String(query.unreadOnly),
    });
    const page = mapPage(
      await this.request<WirePage<WireNotification>>(`/notifications?${params}`, options),
      notification,
    );
    await this.verifyNotificationOwner(options);
    return page;
  }
  private async verifyNotificationOwner(options?: NotificationAccessOptions) {
    if (!options?.expectedUserId || !options.expectedRole) return;
    const currentUser = await this.me(options);
    if (currentUser.id !== options.expectedUserId || currentUser.role !== options.expectedRole) {
      this.invalidateSession();
      throw new ApiError('ACCOUNT_CHANGED', 401);
    }
  }
  async markNotificationRead(id: string, options?: NotificationAccessOptions) {
    const currentUser = await this.me(options);
    if (
      options?.expectedUserId &&
      (currentUser.id !== options.expectedUserId || currentUser.role !== options.expectedRole)
    ) {
      this.invalidateSession();
      throw new ApiError('ACCOUNT_CHANGED', 401);
    }
    if (currentUser.role !== 'STUDENT' && currentUser.role !== 'STAFF')
      throw new ApiError('FORBIDDEN_ROLE', 403);
    const result = await this.write<{ id: string; read_at: string }>(
      `/notifications/${encodeURIComponent(id)}/read`,
      {},
      options,
      {},
      currentUser.id,
      undefined,
      'POST',
      currentUser.role,
    );
    requireContract(result && result.id === id && typeof result.read_at === 'string');
    return { id: result.id, readAt: result.read_at };
  }
  cancel(id: string, reason: string | undefined, options?: ServiceRequestOptions) {
    return this.write<{ id: string; status: 'CANCELLED'; cancelled_at: string }>(
      `/appointments/${encodeURIComponent(id)}/cancel`,
      reason === undefined ? {} : { reason },
      options,
    );
  }
  async staffAppointments(
    query: StaffAppointmentQuery = {},
    options?: ServiceRequestOptions,
  ): Promise<StaffPage<StaffAppointment>> {
    const params = new URLSearchParams({
      page: String(query.page ?? 1),
      page_size: String(query.pageSize ?? 20),
      ...(query.status ? { status: query.status } : {}),
      ...(query.date ? { date: query.date } : {}),
    });
    return mapPage(
      await this.request<WirePage<WireStaffAppointment>>(
        `/staff/me/appointments?${params}`,
        options,
      ),
      staffAppointment,
    );
  }
  async staffAppointment(
    id: string,
    options?: ServiceRequestOptions,
  ): Promise<StaffAppointmentDetail> {
    const data = await this.request<WireStaffAppointmentDetail>(
      `/staff/me/appointments/${encodeURIComponent(id)}`,
      options,
    );
    return {
      ...staffAppointment(data),
      description: data.description,
      cancelledBy: data.cancelled_by,
      cancelReason: data.cancel_reason,
      outcomeBy: data.outcome_by,
    };
  }
  cancelStaffAppointment(id: string, reason: string | undefined, options: StaffWriteOptions) {
    return this.write<{ id: string; status: 'CANCELLED'; cancelled_at: string }>(
      `/staff/me/appointments/${encodeURIComponent(id)}/cancel`,
      reason === undefined ? {} : { reason },
      options,
      {},
      options.expectedUserId,
      undefined,
      'POST',
      'STAFF',
    );
  }
  recordStaffOutcome(id: string, status: StaffOutcomeStatus, options: StaffWriteOptions) {
    return this.write<{ id: string; status: StaffOutcomeStatus; outcome_at: string }>(
      `/staff/me/appointments/${encodeURIComponent(id)}/outcome`,
      { status },
      options,
      {},
      options.expectedUserId,
      undefined,
      'POST',
      'STAFF',
    );
  }
  async staffPurposes(page = 1, options?: ServiceRequestOptions): Promise<StaffPage<StaffPurpose>> {
    return mapPage(
      await this.request<WirePage<WireStaffPurpose>>(
        `/staff/me/purposes?page=${page}&page_size=20`,
        options,
      ),
      staffPurpose,
    );
  }
  async setStaffPurpose(
    id: string,
    isAccepting: boolean,
    options: StaffWriteOptions,
  ): Promise<StaffPurpose> {
    return staffPurpose(
      await this.write<WireStaffPurpose>(
        `/staff/me/purposes/${encodeURIComponent(id)}`,
        { is_accepting: isAccepting },
        options,
        {},
        options.expectedUserId,
        undefined,
        'PUT',
        'STAFF',
      ),
    );
  }
  async staffUnavailability(
    query: StaffUnavailabilityQuery = {},
    options?: ServiceRequestOptions,
  ): Promise<StaffPage<StaffUnavailability>> {
    const params = new URLSearchParams({
      ...(query.from ? { from: query.from } : {}),
      ...(query.to ? { to: query.to } : {}),
      page: String(query.page ?? 1),
      page_size: String(query.pageSize ?? 20),
    });
    return mapPage(
      await this.request<WirePage<WireStaffUnavailability>>(
        `/staff/me/unavailability?${params}`,
        options,
      ),
      staffUnavailability,
    );
  }
  async createStaffUnavailability(
    startsAt: string,
    endsAt: string,
    options: StaffWriteOptions,
  ): Promise<StaffUnavailability> {
    return staffUnavailability(
      await this.write<WireStaffUnavailability>(
        '/staff/me/unavailability',
        { starts_at: startsAt, ends_at: endsAt },
        options,
        {},
        options.expectedUserId,
        undefined,
        'POST',
        'STAFF',
      ),
    );
  }
  deleteStaffUnavailability(id: string, options: StaffWriteOptions) {
    return this.write<void>(
      `/staff/me/unavailability/${encodeURIComponent(id)}`,
      undefined,
      options,
      {},
      options.expectedUserId,
      undefined,
      'DELETE',
      'STAFF',
    );
  }
}
