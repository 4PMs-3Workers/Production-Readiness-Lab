import { isBusinessDate } from '@/lib/business-time';
import type { AvailableSlot, Purpose, Staff } from '@/types/booking';

export interface BookingSelection {
  readonly purpose: Purpose | null;
  readonly staff: Staff | null;
  readonly date: string;
  readonly slot: AvailableSlot | null;
  readonly description: string;
}

type BookingStep = 0 | 1 | 2 | 3;
interface BookingState {
  readonly step: BookingStep;
  readonly selection: BookingSelection;
}

type BookingAction =
  | { type: 'purposeSelected'; purpose: Purpose }
  | { type: 'descriptionChanged'; description: string }
  | { type: 'staffSelected'; purposeId: string; staff: Staff }
  | { type: 'dateSelected'; purposeId: string; staffId: string; date: string }
  | { type: 'slotSelected'; slot: AvailableSlot }
  | { type: 'stepChanged'; step: number }
  | { type: 'bookingConflict' };

const emptySelection: BookingSelection = {
  purpose: null,
  staff: null,
  date: '',
  slot: null,
  description: '',
};
export const initialBookingState: BookingState = { step: 0, selection: emptySelection };

function hasValidPurpose(selection: BookingSelection): boolean {
  return Boolean(
    selection.purpose && (!selection.purpose.requiresFreeText || selection.description.trim()),
  );
}

function matchesSelection(selection: BookingSelection, slot: AvailableSlot): boolean {
  return (
    slot.purposeId === selection.purpose?.id &&
    slot.staffId === selection.staff?.id &&
    slot.date === selection.date
  );
}

export function isCompleteSelection(selection: BookingSelection): selection is BookingSelection & {
  purpose: Purpose;
  staff: Staff;
  slot: AvailableSlot;
} {
  return Boolean(
    hasValidPurpose(selection) &&
    selection.staff &&
    isBusinessDate(selection.date) &&
    selection.slot &&
    matchesSelection(selection, selection.slot),
  );
}

export function canVisitStep(selection: BookingSelection, step: number): step is BookingStep {
  if (step === 0) return true;
  if (step === 1) return hasValidPurpose(selection);
  if (step === 2) return hasValidPurpose(selection) && selection.staff !== null;
  return step === 3 && isCompleteSelection(selection);
}

export function bookingReducer(state: BookingState, action: BookingAction): BookingState {
  const { selection } = state;
  switch (action.type) {
    case 'purposeSelected':
      return selection.purpose?.id === action.purpose.id
        ? state
        : {
            step: 0,
            selection: { ...emptySelection, purpose: action.purpose },
          };
    case 'descriptionChanged':
      return selection.purpose?.requiresFreeText
        ? {
            step: 0,
            selection: { ...selection, description: action.description },
          }
        : state;
    case 'staffSelected':
      if (selection.purpose?.id !== action.purposeId || selection.staff?.id === action.staff.id)
        return state;
      return { step: 1, selection: { ...selection, staff: action.staff, date: '', slot: null } };
    case 'dateSelected':
      if (
        selection.purpose?.id !== action.purposeId ||
        selection.staff?.id !== action.staffId ||
        selection.date === action.date ||
        !isBusinessDate(action.date)
      )
        return state;
      return { step: 2, selection: { ...selection, date: action.date, slot: null } };
    case 'slotSelected':
      return matchesSelection(selection, action.slot)
        ? {
            step: 2,
            selection: { ...selection, slot: action.slot },
          }
        : state;
    case 'stepChanged':
      return action.step <= state.step + 1 && canVisitStep(selection, action.step)
        ? { ...state, step: action.step }
        : state;
    case 'bookingConflict':
      return { step: 2, selection: { ...selection, slot: null } };
  }
}
