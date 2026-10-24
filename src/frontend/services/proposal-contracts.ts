import type { AppointmentStatus, Page } from '@/types/api';
import type { ServiceRequestOptions } from './contracts';

export type ProposalStatus =
  'PENDING' | 'ACCEPTED' | 'DECLINED' | 'EXPIRED' | 'SUPERSEDED' | 'CANCELLED';

export interface ProposalHistoryItem {
  readonly id: string;
  readonly appointmentId: string;
  readonly createdBy: string;
  readonly createdVersion: number | null;
  readonly proposedStartsAt: string;
  readonly proposedEndsAt: string;
  readonly proposedLocation: string | null;
  readonly status: ProposalStatus;
  readonly createdAt: string;
  readonly expiresAt: string;
  readonly resolvedAt: string | null;
  readonly resolvedBy: string | null;
}

export interface ProposalContext {
  readonly appointmentId: string;
  readonly appointmentStatus: AppointmentStatus;
  readonly proposalVersion: number;
  readonly pendingProposalId: string | null;
}

export interface ProposalHistoryPage extends Page<ProposalHistoryItem> {
  readonly context: ProposalContext;
}

/** Every field is required. Null clears the location; keeping it sends its exact value. */
export interface ProposalCreateBody {
  readonly expected_proposal_version: number;
  readonly expected_pending_proposal_id: string | null;
  readonly proposed_starts_at: string;
  readonly proposed_location: string | null;
}

/** This confirms creation of a historical proposal, never its current state. */
export interface ProposalCreateReceipt {
  readonly id: string;
  readonly appointmentId: string;
  readonly createdVersion: number;
  readonly createdAt: string;
  readonly expiresAt: string;
}

export interface ProposalResolution {
  readonly id: string;
  readonly appointmentId: string;
  readonly status: 'ACCEPTED' | 'DECLINED';
  readonly resolvedAt: string;
  readonly resolvedBy: string;
}

export interface ProposalWriteOptions extends ServiceRequestOptions {
  readonly expectedUserId: string;
  /** Called only when dispatching the business POST, after account and CSRF checks. */
  readonly onDispatch?: () => void;
}

export interface ProposalAccessOptions extends ProposalWriteOptions {
  readonly expectedRole: 'STUDENT' | 'STAFF';
}
