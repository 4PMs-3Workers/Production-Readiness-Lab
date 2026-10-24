import { parseInstant } from '@/lib/business-time';
import type { AppointmentStatus } from '@/types/api';
import type {
  ProposalContext,
  ProposalCreateReceipt,
  ProposalHistoryItem,
  ProposalHistoryPage,
  ProposalResolution,
  ProposalStatus,
} from '../proposal-contracts';
import { ApiError } from './errors';

function requireContract(condition: unknown): asserts condition {
  if (!condition) throw new ApiError('SERVICE_UNAVAILABLE');
}

function record(value: unknown): Record<string, unknown> {
  requireContract(value !== null && typeof value === 'object' && !Array.isArray(value));
  return value as Record<string, unknown>;
}

function identifier(value: unknown): string {
  requireContract(typeof value === 'string' && value.length > 0);
  return value;
}

function nullableIdentifier(value: unknown): string | null {
  return value === null ? null : identifier(value);
}

function version(value: unknown, minimum = 0): number {
  requireContract(Number.isSafeInteger(value) && (value as number) >= minimum);
  return value as number;
}

function timestamp(value: unknown): string {
  requireContract(typeof value === 'string');
  try {
    parseInstant(value);
  } catch {
    throw new ApiError('SERVICE_UNAVAILABLE');
  }
  // Preserve the server's offset and microseconds instead of round-tripping through Date.
  return value;
}

const appointmentStatuses: readonly AppointmentStatus[] = [
  'UPCOMING',
  'CHANGE_PENDING',
  'CANCELLED',
  'COMPLETED',
  'NO_SHOW',
];
const proposalStatuses: readonly ProposalStatus[] = [
  'PENDING',
  'ACCEPTED',
  'DECLINED',
  'EXPIRED',
  'SUPERSEDED',
  'CANCELLED',
];

function proposalContext(value: unknown, appointmentId: string): ProposalContext {
  const data = record(value);
  requireContract(
    data.appointment_id === appointmentId &&
      appointmentStatuses.includes(data.appointment_status as AppointmentStatus),
  );
  const pendingProposalId = nullableIdentifier(data.pending_proposal_id);
  requireContract((data.appointment_status === 'CHANGE_PENDING') === (pendingProposalId !== null));
  return {
    appointmentId,
    appointmentStatus: data.appointment_status as AppointmentStatus,
    proposalVersion: version(data.proposal_version),
    pendingProposalId,
  };
}

function proposalItem(value: unknown, appointmentId: string): ProposalHistoryItem {
  const data = record(value);
  requireContract(
    data.appointment_id === appointmentId &&
      proposalStatuses.includes(data.status as ProposalStatus) &&
      (data.proposed_location === null || typeof data.proposed_location === 'string'),
  );
  return {
    id: identifier(data.id),
    appointmentId,
    createdBy: identifier(data.created_by),
    createdVersion: data.created_version === null ? null : version(data.created_version, 1),
    proposedStartsAt: timestamp(data.proposed_starts_at),
    proposedEndsAt: timestamp(data.proposed_ends_at),
    proposedLocation: data.proposed_location as string | null,
    status: data.status as ProposalStatus,
    createdAt: timestamp(data.created_at),
    expiresAt: timestamp(data.expires_at),
    resolvedAt: data.resolved_at === null ? null : timestamp(data.resolved_at),
    resolvedBy: nullableIdentifier(data.resolved_by),
  };
}

export function parseProposalHistory(value: unknown, appointmentId: string): ProposalHistoryPage {
  const data = record(value);
  requireContract(Array.isArray(data.items));
  const page = version(data.page, 1);
  const pageSize = version(data.page_size, 1);
  const total = version(data.total);
  requireContract(pageSize <= 100 && data.items.length <= pageSize);
  return {
    items: data.items.map((item) => proposalItem(item, appointmentId)),
    page,
    pageSize,
    total,
    // Context belongs to the response, including empty and later pages.
    context: proposalContext(data.context, appointmentId),
  };
}

export function parseProposalReceipt(value: unknown, appointmentId: string): ProposalCreateReceipt {
  const data = record(value);
  requireContract(data.appointment_id === appointmentId);
  return {
    id: identifier(data.id),
    appointmentId,
    createdVersion: version(data.created_version, 1),
    createdAt: timestamp(data.created_at),
    expiresAt: timestamp(data.expires_at),
  };
}

export function parseProposalResolution(
  value: unknown,
  appointmentId: string,
  proposalId: string,
  action: 'accept' | 'decline',
  userId: string,
): ProposalResolution {
  const data = record(value);
  const status = action === 'accept' ? 'ACCEPTED' : 'DECLINED';
  requireContract(
    data.appointment_id === appointmentId &&
      data.id === proposalId &&
      data.status === status &&
      data.resolved_by === userId,
  );
  return {
    id: proposalId,
    appointmentId,
    status,
    resolvedAt: timestamp(data.resolved_at),
    resolvedBy: userId,
  };
}
