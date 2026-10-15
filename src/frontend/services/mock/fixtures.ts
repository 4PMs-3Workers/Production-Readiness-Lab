import { getBookingDates } from '@/lib/business-time';
import type { AvailableSlot, Purpose, Staff, StudentContext } from '@/types/booking';

export const DEFAULT_DEMO_NOW = '2026-10-13T09:00:00+07:00';

const student: StudentContext = {
  id: 'student-demo-01',
  displayName: 'Sinh viên Demo HUS',
  email: 'student@example.test',
};

const purposes: readonly Purpose[] = [
  {
    id: 'purpose-career',
    code: 'CAREER_INTERNSHIP',
    name: 'Nghề nghiệp và thực tập',
    durationMinutes: 30,
    requiresFreeText: false,
  },
  {
    id: 'purpose-thesis',
    code: 'THESIS_RESEARCH',
    name: 'Khóa luận và nghiên cứu',
    durationMinutes: 60,
    requiresFreeText: false,
  },
  {
    id: 'purpose-academic',
    code: 'ACADEMIC_ADVISOR',
    name: 'Cố vấn học tập',
    durationMinutes: 30,
    requiresFreeText: false,
  },
  {
    id: 'purpose-general',
    code: 'GENERAL_ADVICE',
    name: 'Tư vấn chung',
    durationMinutes: 45,
    requiresFreeText: false,
  },
  {
    id: 'purpose-other',
    code: 'OTHER',
    name: 'Nhu cầu khác',
    durationMinutes: 30,
    requiresFreeText: true,
  },
];

const staff: readonly Staff[] = [
  { id: 'staff-demo-01', displayName: 'Cố vấn Demo 01' },
  { id: 'staff-demo-02', displayName: 'Cố vấn Demo 02' },
  { id: 'staff-demo-03', displayName: 'Cố vấn Demo 03' },
  { id: 'staff-demo-04', displayName: 'Cố vấn Demo 04' },
];

const associations = [
  ['purpose-career', 'staff-demo-01'],
  ['purpose-career', 'staff-demo-02'],
  ['purpose-thesis', 'staff-demo-02'],
  ['purpose-thesis', 'staff-demo-03'],
  ['purpose-academic', 'staff-demo-01'],
  ['purpose-academic', 'staff-demo-04'],
  ['purpose-general', 'staff-demo-03'],
  ['purpose-general', 'staff-demo-04'],
  ['purpose-other', 'staff-demo-04'],
] as const;

// Declared fixture appointments, not an implementation of backend availability rules.
const slotTemplates = [
  { dayOffset: 1, time: '09:00', location: 'Phòng tư vấn demo A101' },
  { dayOffset: 1, time: '09:15', location: null },
  { dayOffset: 1, time: '09:30', location: null },
  { dayOffset: 2, time: '10:00', location: 'Phòng tư vấn demo A101' },
  { dayOffset: 3, time: '14:15', location: null },
  { dayOffset: 4, time: '09:30', location: 'Phòng tư vấn demo A102' },
  { dayOffset: 5, time: '10:15', location: null },
  { dayOffset: 6, time: '14:00', location: 'Phòng tư vấn demo A102' },
  { dayOffset: 7, time: '14:15', location: null },
] as const;

export function createFixtures(now: string) {
  const dates = getBookingDates(now);
  const slots: AvailableSlot[] = associations.flatMap(([purposeId, staffId]) => {
    const purpose = purposes.find((item) => item.id === purposeId)!;
    return slotTemplates.map(({ dayOffset, time, location }) => {
      const date = dates[dayOffset - 1];
      const startsAt = new Date(`${date}T${time}:00+07:00`);
      return {
        id: `${purposeId}:${staffId}:${date}:${time}`,
        purposeId,
        staffId,
        date,
        startsAt: startsAt.toISOString(),
        endsAt: new Date(startsAt.getTime() + purpose.durationMinutes * 60_000).toISOString(),
        location,
      };
    });
  });
  return {
    student: { ...student },
    purposes: purposes.map((item) => ({ ...item })),
    staff: staff.map((item) => ({ ...item })),
    associations: associations.map(([purposeId, staffId]) => ({ purposeId, staffId })),
    slots,
  };
}
