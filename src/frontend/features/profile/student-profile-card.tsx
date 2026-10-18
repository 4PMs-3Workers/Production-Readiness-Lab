import type { StudentProfile } from '@/types/api';
import styles from './profile.module.css';

const genderLabels = {
  MALE: 'Nam',
  FEMALE: 'Nữ',
  OTHER: 'Khác',
  UNDISCLOSED: 'Không cung cấp',
};

export function StudentProfileCard({ student }: { student: StudentProfile }) {
  const fields = [
    ['Họ và tên', student.displayName],
    ['Mã sinh viên', student.studentCode],
    ['Email', student.email],
    ['Giới tính', student.gender ? genderLabels[student.gender] : null],
    ['Khoa', student.faculty],
    ['Ngành', student.major],
    ['Vai trò', 'Sinh viên'],
  ];
  return (
    <div className={styles.profilePage}>
      <header className={styles.heading}>
        <p>HỒ SƠ CỦA BẠN</p>
        <h1>Thông tin sinh viên</h1>
        <span>Thông tin cá nhân và học tập gắn với tài khoản của bạn.</span>
      </header>
      <section className={styles.card} aria-label="Thông tin cá nhân và học tập">
        <div className={styles.identity}>
          <span className={styles.avatar} aria-hidden="true">
            SV
          </span>
          <div>
            <h2>{student.displayName}</h2>
            <p>{student.studentCode?.trim() || 'Chưa cập nhật mã sinh viên'}</p>
          </div>
        </div>
        <dl className={styles.fields}>
          {fields.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value?.trim() || 'Chưa cập nhật'}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
