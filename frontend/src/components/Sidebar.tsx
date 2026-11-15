'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

// Navigation for host pages: a pill column on desktop, a scrolling pill row on phones.
interface SidebarLink {
  href: string;
  label: string;
}

interface SidebarProps {
  links: SidebarLink[];
}

export default function Sidebar({ links }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside className="shrink-0 border-b-[2.5px] border-ink bg-sun-soft md:min-h-[calc(100vh-64px)] md:w-60 md:border-b-0 md:border-r-[2.5px]">
      <ul className="flex gap-2 overflow-x-auto p-3 md:flex-col md:p-4">
        {links.map((link) => {
          const active = pathname === link.href;
          return (
            <li key={link.href} className="shrink-0">
              <Link
                href={link.href}
                aria-current={active ? 'page' : undefined}
                className={`btn btn-sm md:w-full md:justify-start ${active ? 'btn-primary' : 'btn-white'}`}
              >
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
