import type { AppointmentStatus, Page } from '@/types/api';

export type NotificationEventType =
  | 'APPOINTMENT_CREATED'
  | 'APPOINTMENT_CANCELLED'
  | 'APPOINTMENT_OUTCOME_RECORDED'
  | 'CHANGE_PROPOSAL_CREATED'
  | 'CHANGE_PROPOSAL_REPLACED'
  | 'CHANGE_PROPOSAL_ACCEPTED'
  | 'CHANGE_PROPOSAL_DECLINED'
  | 'CHANGE_PROPOSAL_EXPIRED'
  | (string & {});

export interface NotificationItem {
  id: string;
  eventType: NotificationEventType;
  summary: string;
  createdAt: string;
  readAt: string | null;
  appointmentId: string | null;
  appointmentStartsAt: string | null;
  appointmentStatus: AppointmentStatus | null;
}

export interface NotificationQuery {
  page: number;
  pageSize: number;
  unreadOnly: boolean;
}

export type NotificationPage = Page<NotificationItem>;
