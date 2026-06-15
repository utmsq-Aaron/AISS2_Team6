import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
  hover = false,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
}) {
  return (
    <div className={`fd-card ${hover ? "fd-card-hover" : ""} ${className}`}>{children}</div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="fd-label mb-1">{children}</div>;
}
