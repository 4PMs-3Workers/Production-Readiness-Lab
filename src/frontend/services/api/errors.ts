const messages: Record<string, string> = {
  INVALID_CREDENTIALS: 'Email hoặc mật khẩu chưa đúng.',
  LOGIN_THROTTLED: 'Bạn đã thử đăng nhập nhiều lần. Vui lòng chờ rồi thử lại.',
  INVALID_SESSION: 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.',
  FORBIDDEN_ROLE: 'Tài khoản này không có quyền sử dụng cổng sinh viên.',
  INVALID_CSRF: 'Phiên bảo vệ yêu cầu đã thay đổi. Vui lòng thử lại thao tác.',
  INVALID_ORIGIN: 'Không thể xác minh nguồn yêu cầu. Vui lòng kiểm tra địa chỉ cổng tư vấn.',
  NOT_FOUND: 'Không tìm thấy dữ liệu hoặc bạn không có quyền truy cập.',
  BOOKING_CONFLICT: 'Giờ hẹn không còn khả dụng. Vui lòng chọn lại giờ.',
  BOOKING_QUOTA_EXCEEDED: 'Bạn đã có tối đa ba lịch đang giữ chỗ. Hãy kiểm tra các lịch đã đặt.',
  IDEMPOTENCY_CONFLICT:
    'Mã yêu cầu đã gắn với nội dung khác. Hãy kiểm tra lịch sử trước khi đặt thêm.',
  CANCELLATION_DEADLINE_PASSED: 'Đã quá thời hạn hủy lịch hẹn này.',
  APPOINTMENT_STATE_CONFLICT: 'Trạng thái lịch hẹn đã thay đổi và không cho phép hủy.',
  VALIDATION_ERROR:
    'Thông tin chưa hợp lệ hoặc cửa sổ ngày đặt đã thay đổi. Vui lòng kiểm tra lại lựa chọn.',
  ACCOUNT_CHANGED: 'Tài khoản đã thay đổi. Yêu cầu cũ đã được xóa; vui lòng mở lại cổng sinh viên.',
  TIMEOUT: 'Yêu cầu quá thời gian chờ. Vui lòng kiểm tra kết quả rồi thử lại.',
  NETWORK_ERROR: 'Mất kết nối đến dịch vụ. Vui lòng kiểm tra kết nối.',
  SERVICE_UNAVAILABLE: 'Dịch vụ tạm thời chưa sẵn sàng. Vui lòng thử lại sau.',
};

export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status = 0,
    readonly requestId?: string,
    readonly retryAfter?: string,
  ) {
    super(messages[code] ?? 'Không thể hoàn thành yêu cầu. Vui lòng thử lại sau.');
    this.name = 'ApiError';
  }

  get unknownOutcome() {
    return this.status === 0 || this.status >= 500;
  }
}

export function errorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return 'Không thể hoàn thành yêu cầu. Vui lòng thử lại.';
  return `${error.message}${error.retryAfter ? ` Thử lại sau: ${error.retryAfter}.` : ''}${error.requestId ? ` Mã hỗ trợ: ${error.requestId}.` : ''}`;
}
