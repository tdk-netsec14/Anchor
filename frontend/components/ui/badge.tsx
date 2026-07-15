import { HTMLAttributes } from "react";

import { cn } from "@/lib/format";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-fg-muted border-border",
  accent: "bg-accent-soft text-accent border-accent/25",
  success: "bg-success-soft text-success border-success/25",
  warning: "bg-warning-soft text-warning border-warning/25",
  danger: "bg-danger-soft text-danger border-danger/25",
};

type Props = HTMLAttributes<HTMLSpanElement> & { tone?: Tone };

export function Badge({ className, tone = "neutral", ...rest }: Props) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5",
        "font-mono text-[11px] leading-4 tracking-tight",
        TONES[tone],
        className,
      )}
      {...rest}
    />
  );
}
