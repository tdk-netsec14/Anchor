"use client";

import { GithubIcon } from "@/components/ui/GithubIcon";
import { Menu, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Mark } from "@/components/ui/Mark";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { buttonClass } from "@/components/ui/button";
import { cn } from "@/lib/format";

const SECTIONS = [
  { id: "capabilities", label: "Capabilities" },
  { id: "rag", label: "RAG" },
  { id: "routing", label: "Routing" },
  { id: "guardrails", label: "Guardrails" },
  { id: "observability", label: "Observability" },
  { id: "architecture", label: "Architecture" },
];

const REPO = "https://github.com/tdk-netsec14/Anchor";

export function Nav() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // A background menu over a page that scrolls behind it is a trap on mobile.
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 transition-colors",
        scrolled ? "border-b border-border bg-bg/85 backdrop-blur" : "border-b border-transparent",
      )}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-5 sm:px-7">
        <Link href="/" className="flex items-center gap-2.5">
          <Mark />
          <span className="text-[15px] font-semibold tracking-tight">Anchor</span>
        </Link>

        <nav className="hidden flex-1 items-center gap-1 lg:flex">
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              className="rounded-md px-2.5 py-1.5 text-[13px] text-fg-muted transition-colors hover:text-fg"
            >
              {section.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <a
            href={REPO}
            target="_blank"
            rel="noreferrer"
            className="hidden size-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg sm:inline-flex"
            aria-label="Anchor on GitHub"
          >
            <GithubIcon className="size-4" />
          </a>
          <ThemeToggle />
          <Link
            href="/signup"
            className={buttonClass("secondary", "sm", "hidden sm:inline-flex")}
          >
            Sign up
          </Link>
          <Link href="/login" className={buttonClass("primary", "sm", "hidden sm:inline-flex")}>
            Open Anchor
          </Link>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            className="inline-flex size-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg lg:hidden"
          >
            {open ? <X className="size-4" /> : <Menu className="size-4" />}
          </button>
        </div>
      </div>

      {open ? (
        <div className="border-t border-border bg-bg lg:hidden">
          <nav className="mx-auto flex max-w-6xl flex-col px-5 py-2 sm:px-7">
            {SECTIONS.map((section) => (
              <a
                key={section.id}
                href={`#${section.id}`}
                onClick={() => setOpen(false)}
                className="rounded-md px-2 py-2.5 text-[14px] text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
              >
                {section.label}
              </a>
            ))}
            <div className="flex items-center gap-2 border-t border-border py-3">
              <Link href="/signup" className={buttonClass("secondary", "sm", "flex-1")}>
                Sign up
              </Link>
              <Link href="/login" className={buttonClass("primary", "sm", "flex-1")}>
                Open Anchor
              </Link>
              <a
                href={REPO}
                target="_blank"
                rel="noreferrer"
                className={buttonClass("secondary", "sm")}
              >
                <GithubIcon className="size-3.5" />
                GitHub
              </a>
            </div>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
