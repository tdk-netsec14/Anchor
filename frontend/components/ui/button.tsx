import { ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-fg hover:bg-accent-hover border border-transparent shadow-sm",
  secondary:
    "bg-surface text-fg border border-border hover:bg-surface-2 hover:border-border-strong",
  ghost: "bg-transparent text-fg-muted hover:text-fg hover:bg-surface-2 border border-transparent",
  danger: "bg-transparent text-danger border border-danger/35 hover:bg-danger-soft",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  lg: "h-11 px-5 text-[15px] gap-2",
};

/** Exported so links can adopt button styling without nesting anchors in buttons. */
export function buttonClass(
  variant: Variant = "secondary",
  size: Size = "md",
  className?: string,
) {
  return cn(
    "inline-flex items-center justify-center rounded-lg font-medium transition-colors",
    "disabled:pointer-events-none disabled:opacity-50",
    VARIANTS[variant],
    SIZES[size],
    className,
  );
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
};

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { className, variant = "secondary", size = "md", type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClass(variant, size, className)}
      {...rest}
    />
  );
});
