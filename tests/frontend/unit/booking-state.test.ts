// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  bookingReducer,
  canVisitStep,
  initialBookingState,
  isCompleteSelection,
} from '@/features/booking/booking-state';
import type { AvailableSlot, Purpose, Staff } from '@/types/booking';
import type { BookingContext } from '@/types/api';

const purpose: Purpose = {
  id: 'career',
  code: 'CAREER_INTERNSHIP',
  name: 'Nghề nghiệp',
  durationMinutes: 30,
  requiresFreeText: false,
};
const other: Purpose = { ...purpose, id: 'other', code: 'OTHER', requiresFreeText: true };
const staff: Staff = { id: 'staff-01', displayName: 'Cố vấn Demo 01' };
const slot: AvailableSlot = {
  id: 'slot-01',
  purposeId: purpose.id,
  staffId: staff.id,
  date: '2026-10-14',
  startsAt: '2026-10-14T02:00:00Z',
  endsAt: '2026-10-14T02:30:00Z',
  location: null,
};
const context: BookingContext = {
  timezone: 'Asia/Ho_Chi_Minh',
  businessDate: '2026-10-13',
  minBookingDate: '2026-10-14',
  maxBookingDate: '2026-10-20',
};

function completedDraft(selectedPurpose = purpose) {
  let state = bookingReducer(initialBookingState, {
    type: 'purposeSelected',
    purpose: selectedPurpose,
  });
  if (selectedPurpose.requiresFreeText)
    state = bookingReducer(state, { type: 'descriptionChanged', description: 'Kế hoạch học tập' });
  state = bookingReducer(state, { type: 'stepChanged', step: 1 });
  state = bookingReducer(state, { type: 'staffSelected', purposeId: selectedPurpose.id, staff });
  state = bookingReducer(state, { type: 'stepChanged', step: 2 });
  state = bookingReducer(state, {
    type: 'dateSelected',
    purposeId: selectedPurpose.id,
    staffId: staff.id,
    date: slot.date,
  });
  state = bookingReducer(state, {
    type: 'slotSelected',
    slot: { ...slot, purposeId: selectedPurpose.id },
  });
  return bookingReducer(state, { type: 'stepChanged', step: 3 });
}

describe('booking draft rules', () => {
  it('blocks incomplete steps and requires meaningful OTHER text before progressing', () => {
    expect(bookingReducer(initialBookingState, { type: 'stepChanged', step: 1 })).toBe(
      initialBookingState,
    );
    let state = bookingReducer(initialBookingState, { type: 'purposeSelected', purpose: other });
    state = bookingReducer(state, { type: 'descriptionChanged', description: '  \n ' });
    expect(canVisitStep(state.selection, 1)).toBe(false);
    state = bookingReducer(state, { type: 'descriptionChanged', description: 'Trao đổi học tập' });
    expect(canVisitStep(state.selection, 1)).toBe(true);
    expect(canVisitStep(state.selection, 2)).toBe(false);
    expect(bookingReducer(state, { type: 'stepChanged', step: 3 })).toBe(state);
    expect(canVisitStep(state.selection, -1)).toBe(false);
    expect(canVisitStep(state.selection, 4)).toBe(false);
  });

  it('preserves a complete draft when navigating back and forward without changes', () => {
    const review = completedDraft();
    expect(review.step).toBe(3);
    expect(isCompleteSelection(review.selection)).toBe(true);
    let state = bookingReducer(review, { type: 'stepChanged', step: 0 });
    expect(state.selection).toBe(review.selection);
    state = bookingReducer(state, { type: 'stepChanged', step: 1 });
    state = bookingReducer(state, { type: 'stepChanged', step: 2 });
    state = bookingReducer(state, { type: 'stepChanged', step: 3 });
    expect(state.selection).toBe(review.selection);
    expect(state.step).toBe(3);
  });

  it('starts a new booking with an empty draft after an intent is confirmed', () => {
    expect(bookingReducer(completedDraft(other), { type: 'reset' })).toEqual(initialBookingState);
  });

  it('clears staff, date, slot and old OTHER text when the purpose changes', () => {
    const state = bookingReducer(completedDraft(other), { type: 'purposeSelected', purpose });
    expect(state).toEqual({
      step: 0,
      selection: { purpose, staff: null, date: '', slot: null, description: '' },
    });
  });

  it('retains purpose/text but clears date and slot when the adviser changes', () => {
    const state = bookingReducer(completedDraft(other), {
      type: 'staffSelected',
      purposeId: other.id,
      staff: { ...staff, id: 'staff-02' },
    });
    expect(state.step).toBe(1);
    expect(state.selection).toMatchObject({
      purpose: other,
      description: 'Kế hoạch học tập',
      date: '',
      slot: null,
    });
    expect(canVisitStep(state.selection, 3)).toBe(false);
  });

  it('clears only the slot when a different date is selected', () => {
    const state = bookingReducer(completedDraft(), {
      type: 'dateSelected',
      purposeId: purpose.id,
      staffId: staff.id,
      date: '2026-10-15',
    });
    expect(state).toEqual({
      step: 2,
      selection: { purpose, staff, date: '2026-10-15', slot: null, description: '' },
    });
  });

  it('keeps dependent choices when the same purpose, adviser or date is selected again', () => {
    const state = completedDraft();
    expect(bookingReducer(state, { type: 'purposeSelected', purpose })).toBe(state);
    expect(bookingReducer(state, { type: 'staffSelected', purposeId: purpose.id, staff })).toBe(
      state,
    );
    expect(
      bookingReducer(state, {
        type: 'dateSelected',
        purposeId: purpose.id,
        staffId: staff.id,
        date: slot.date,
      }),
    ).toBe(state);
  });

  it('ignores selection events and slots that belong to an older query', () => {
    const state = completedDraft();
    expect(bookingReducer(state, { type: 'staffSelected', purposeId: other.id, staff })).toBe(
      state,
    );
    expect(
      bookingReducer(state, {
        type: 'dateSelected',
        purposeId: purpose.id,
        staffId: 'old-staff',
        date: '2026-10-15',
      }),
    ).toBe(state);
    for (const mismatch of [
      { purposeId: other.id },
      { staffId: 'old-staff' },
      { date: '2026-10-15' },
    ]) {
      expect(bookingReducer(state, { type: 'slotSelected', slot: { ...slot, ...mismatch } })).toBe(
        state,
      );
      expect(isCompleteSelection({ ...state.selection, slot: { ...slot, ...mismatch } })).toBe(
        false,
      );
    }
  });

  it('returns a conflict to time selection and preserves all other review information', () => {
    const review = completedDraft(other);
    const state = bookingReducer(review, { type: 'bookingConflict' });
    expect(state).toEqual({ step: 2, selection: { ...review.selection, slot: null } });
    expect(canVisitStep(state.selection, 3)).toBe(false);
  });

  it('preserves the full draft and current step when refreshed booking context is unchanged', () => {
    const review = completedDraft(other);
    const next = { ...context };
    const state = bookingReducer(review, { type: 'contextChanged', previous: context, next });
    expect(state).toBe(review);
  });

  it('reconciles only date and slot choices when changed context invalidates the date', () => {
    const review = completedDraft(other);
    const next: BookingContext = {
      ...context,
      timezone: 'America/Los_Angeles',
      minBookingDate: '2026-10-15',
      maxBookingDate: '2026-10-25',
    };
    const state = bookingReducer(review, { type: 'contextChanged', previous: context, next });
    expect(state).toEqual({
      step: 2,
      selection: {
        ...review.selection,
        date: '',
        slot: null,
      },
    });
  });

  it('retains an in-range date while clearing its slot after a timezone change', () => {
    const review = completedDraft(other);
    const next: BookingContext = { ...context, timezone: 'America/Los_Angeles' };
    const state = bookingReducer(review, { type: 'contextChanged', previous: context, next });
    expect(state).toEqual({
      step: 2,
      selection: { ...review.selection, slot: null },
    });
  });

  it.each([
    { name: 'remounted empty draft', selection: initialBookingState.selection, step: 0 },
    { name: 'missing adviser', selection: { ...initialBookingState.selection, purpose }, step: 1 },
    {
      name: 'missing required description',
      selection: { ...completedDraft(other).selection, description: '  ' },
      step: 0,
    },
  ])('recovers a conflict at a reachable step for $name', ({ selection, step }) => {
    const recovered = bookingReducer({ step: 0, selection }, { type: 'bookingConflict' });
    expect(recovered).toEqual({ step, selection: { ...selection, slot: null } });
    expect(canVisitStep(recovered.selection, recovered.step)).toBe(true);
    expect(isCompleteSelection(recovered.selection)).toBe(false);
  });
});
