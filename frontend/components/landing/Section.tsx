import type { ReactNode } from "react";

/** A titled block on the marketing page. */
export function Section({
  id,
  eyebrow,
  title,
  lede,
  children,
  className = "",
}: {
  id?: string;
  eyebrow: string;
  title: string;
  lede: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`border-t border-border py-16 sm:py-20 ${className}`}>
      <div className="mx-auto max-w-6xl px-5 sm:px-7">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">
          {eyebrow}
        </p>
        <h2 className="mt-2.5 max-w-2xl text-[26px] font-semibold leading-[1.2] tracking-tight sm:text-[30px]">
          {title}
        </h2>
        <p className="mt-3 max-w-2xl text-[14.5px] leading-relaxed text-fg-muted">{lede}</p>
        <div className="mt-8">{children}</div>
      </div>
    </section>
  );
}

export function FeatureGrid({ children }: { children: ReactNode }) {
  return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{children}</div>;
}

export function FeatureCard({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-3 flex size-8 items-center justify-center rounded-lg bg-accent-soft text-accent">
        {icon}
      </div>
      <h3 className="text-[14px] font-semibold tracking-tight">{title}</h3>
      <p className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">{children}</p>
    </div>
  );
}
