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
import { ApiError, BookingRejection } from './errors';

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
    body: object,
    options?: ServiceRequestOptions,
    headers: Record<string, string> = {},
    expectedUserId?: string,
    onDispatch?: () => void,
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
      if (csrf.user_id !== expectedUserId) throw new ApiError('ACCOUNT_CHANGED', 401);
      const verified = await this.me(scopedOptions);
      if (verified.id !== expectedUserId || verified.role !== 'STUDENT')
        throw new ApiError('ACCOUNT_CHANGED', 401);
    }
    signal.throwIfAborted();
    return this.request(
      path,
      scopedOptions,
      {
        method: 'POST',
        body: JSON.stringify(body),
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
    const data = await this.request<{
      timezone: string;
      business_date: string;
      min_booking_date: string;
      max_booking_date: string;
    }>('/booking-context', options);
    return {
      timezone: data.timezone,
      businessDate: data.business_date,
      minBookingDate: data.min_booking_date,
      maxBookingDate: data.max_booking_date,
    };
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
  cancel(id: string, reason: string | undefined, options?: ServiceRequestOptions) {
    return this.write<{ id: string; status: 'CANCELLED'; cancelled_at: string }>(
      `/appointments/${encodeURIComponent(id)}/cancel`,
      reason === undefined ? {} : { reason },
      options,
    );
  }
}
