import { InputHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/format";

type Props = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, Props>(function Input(
  { className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-fg",
        "placeholder:text-fg-subtle focus:border-accent focus:outline-none",
        "disabled:opacity-60",
        className,
      )}
      {...rest}
    />
  );
});
