// Sticker-style card for dashboard widgets
interface CardProps {
  title: string;
  children: React.ReactNode;
  className?: string;
  /** Optional coloured header strip, e.g. "block-sun". */
  accent?: string;
}

export default function Card({ title, children, className = '', accent }: CardProps) {
  return (
    <div className={`sticker overflow-hidden ${className}`}>
      {accent && <div className={`${accent} h-3 border-b-[2.5px] border-ink`} />}
      <div className="p-5">
        <h3 className="font-display text-xl font-extrabold leading-tight">{title}</h3>
        <div className="mt-2">{children}</div>
      </div>
    </div>
  );
}
