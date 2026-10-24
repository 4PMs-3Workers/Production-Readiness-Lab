/** System cancellation reasons share the field with human-entered text. */
export function formatCancellationReason(reason: string): string {
  if (reason === 'CHANGE_PROPOSAL_EXPIRED') return 'Đề xuất đổi lịch đã hết hạn phản hồi.';
  if (reason === 'CHANGE_PROPOSAL_DECLINED') return 'Sinh viên đã từ chối đề xuất đổi lịch.';
  return reason;
}
