import Link from 'next/link';
import type { ReactNode } from 'react';

interface DemoPageProps {
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
  action: { href: string; label: string };
}

export function DemoPage({ eyebrow, title, description, children, action }: DemoPageProps) {
  return (
    <section className="page-grid" aria-labelledby="page-title">
      <div className="page-intro">
        <p className="eyebrow">{eyebrow}</p>
        <h1 id="page-title">{title}</h1>
        <p className="page-description">{description}</p>
        <div className="intro-note">
          <span className="note-line" aria-hidden="true" />
          <p>Một cuộc trò chuyện, thêm một hướng đi cho hành trình của bạn.</p>
        </div>
      </div>
      <div className="content-card">
        {children}
        <Link className="primary-link" href={action.href}>
          {action.label}
          <span aria-hidden="true">→</span>
        </Link>
      </div>
    </section>
  );
}
