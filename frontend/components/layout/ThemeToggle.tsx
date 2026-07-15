"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/format";

const STORAGE_KEY = "anchor-theme";

/**
 * Theme is applied by a blocking script in the document head (see layout.tsx),
 * so this component only mirrors the resulting state for the icon.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const [dark, setDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setMounted(true);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? "dark" : "light");
    } catch {
      // Private-mode browsers reject writes; the toggle still works for the
      // current page, it just will not persist.
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-lg text-fg-muted",
        "transition-colors hover:bg-surface-2 hover:text-fg",
        className,
      )}
    >
      {mounted && dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

/** Injected before paint so a dark-mode reload does not flash white. */
export const themeInitScript = `(function(){try{var t=localStorage.getItem("${STORAGE_KEY}");
if(t==="dark"||(!t&&window.matchMedia("(prefers-color-scheme: dark)").matches)){document.documentElement.classList.add("dark")}}catch(e){}})();`;
