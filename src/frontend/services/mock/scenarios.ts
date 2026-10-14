import type { DemoScenario, DemoScenarioId } from '@/types/demo';

const defaults = {
  delayMs: 200,
  login: 'success',
  purposes: 'success',
  staff: 'success',
  slots: 'success',
  booking: 'success',
  firstSelectionExtraDelayMs: 0,
} as const;

const definitions: Record<
  DemoScenarioId,
  Pick<DemoScenario, 'label' | 'description'> & Partial<Omit<DemoScenario, 'id'>>
> = {
  success: { label: 'Thành công', description: 'Các bước đều trả dữ liệu demo thành công.' },
  slow: { label: 'Phản hồi chậm', description: 'Mỗi request chờ 1,5 giây.', delayMs: 1500 },
  'invalid-login': {
    label: 'Sai thông tin đăng nhập',
    description: 'Đăng nhập luôn báo thông tin không hợp lệ.',
    login: 'invalid',
  },
  'login-error': {
    label: 'Lỗi dịch vụ đăng nhập',
    description: 'Đăng nhập luôn báo lỗi dịch vụ.',
    login: 'error',
  },
  'empty-purposes': {
    label: 'Chưa có nhu cầu tư vấn',
    description: 'Danh sách nhu cầu rỗng.',
    purposes: 'empty',
  },
  'empty-staff': {
    label: 'Chưa có nhân viên',
    description: 'Danh sách nhân viên cho nhu cầu đã chọn rỗng.',
    staff: 'empty',
  },
  'empty-slots': {
    label: 'Chưa có giờ trống',
    description: 'Danh sách giờ trống rỗng.',
    slots: 'empty',
  },
  'purposes-error': {
    label: 'Lỗi tải nhu cầu một lần',
    description: 'Lần tải nhu cầu đầu thất bại; thử lại thành công.',
    purposes: 'error-once',
  },
  'staff-error': {
    label: 'Lỗi tải nhân viên một lần',
    description: 'Lần tải nhân viên đầu thất bại; thử lại thành công.',
    staff: 'error-once',
  },
  'slots-error': {
    label: 'Lỗi tải giờ trống một lần',
    description: 'Lần tải giờ trống đầu thất bại; thử lại thành công.',
    slots: 'error-once',
  },
  'booking-error': {
    label: 'Lỗi xác nhận lịch',
    description: 'Xác nhận luôn thất bại; không trả kết quả thành công.',
    booking: 'error',
  },
  'conflict-once': {
    label: 'Xung đột giờ một lần',
    description: 'Lần xác nhận đầu báo xung đột; tải lại để chọn giờ khác.',
    booking: 'conflict-once',
  },
  'stale-response': {
    label: 'Phản hồi cũ đến sau',
    description: 'Request nhân viên/giờ trống đầu chậm hơn các request tiếp theo 1,5 giây.',
    delayMs: 50,
    firstSelectionExtraDelayMs: 1500,
  },
};

export function getDemoScenario(id: DemoScenarioId): DemoScenario {
  if (!Object.hasOwn(definitions, id)) throw new RangeError('Unknown demo scenario.');
  const definition = definitions[id];
  if (!definition) throw new RangeError('Unknown demo scenario.');
  return { ...defaults, ...definition, id };
}

/** Safe metadata for a future demo-controls panel; fixture data stays behind services. */
export const DEMO_SCENARIOS = Object.freeze(
  (Object.keys(definitions) as DemoScenarioId[]).map((id) => {
    const { label, description } = definitions[id];
    return Object.freeze({ id, label, description });
  }),
);
