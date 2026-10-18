'use client';

import Link from 'next/link';
import { ApiStudentProfile } from '@/features/api/api-student-profile';
import { useDemo } from '@/features/demo/demo-provider';
import { isMockMode } from '@/lib/application-mode';
import { StudentProfileCard } from './student-profile-card';

export function ProfileView() {
  return isMockMode() ? <MockStudentProfile /> : <ApiStudentProfile />;
}

function MockStudentProfile() {
  const { student } = useDemo();
  return student ? (
    <StudentProfileCard
      student={{ ...student, studentCode: null, faculty: null, major: null, gender: null }}
    />
  ) : (
    <section>
      <h1>Thông tin sinh viên</h1>
      <p>Bạn chưa có phiên đăng nhập demo.</p>
      <Link href="/login">Đăng nhập demo</Link>
    </section>
  );
}
