"use client";

import { GithubIcon } from "@/components/ui/GithubIcon";
import { Mark } from "@/components/ui/Mark";
import {
  Activity as ActivityIcon,
  BarChart3,
  BookOpen,
  Bot,
  LogOut,
  Settings as SettingsIcon,
  Shield,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/format";

const WORKSPACE = [
  { href: "/assistant", label: "Assistant", icon: Bot },
  { href: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  { href: "/activity", label: "Activity", icon: ActivityIcon },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

const REPO_URL = "https://github.com/tdk-netsec14/Anchor";

/** The deployment URL is public information, so the docs link can use it. */
const API_DOCS_URL =
  process.env.NEXT_PUBLIC_API_URL?.trim() || "http://127.0.0.1:8000";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { session, signOut } = useAuth();

  return (
    <div className="flex h-full flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center justify-between px-4">
        <Link href="/assistant" className="flex items-center gap-2.5" onClick={onNavigate}>
          <Mark />
          <span className="text-[15px] font-semibold tracking-tight">Anchor</span>
        </Link>
        {onNavigate ? (
          <button
            type="button"
            onClick={onNavigate}
            aria-label="Close navigation"
            className="inline-flex size-8 items-center justify-center rounded-lg text-fg-muted hover:bg-surface-2 lg:hidden"
          >
            <X className="size-4" />
          </button>
        ) : null}
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-2">
        <p className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
          Workspace
        </p>
        <ul className="space-y-0.5">
          {WORKSPACE.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] transition-colors",
                    active
                      ? "bg-accent-soft font-medium text-accent"
                      : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-border p-3">
        <div className="mb-2 space-y-0.5">
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <GithubIcon className="size-4 shrink-0" />
            GitHub
          </a>
          <a
            href={`${API_DOCS_URL.replace(/\/+$/, "")}/docs`}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <Shield className="size-4 shrink-0" />
            API Docs
          </a>
        </div>

        <div className="flex items-center gap-2.5 rounded-lg bg-surface-2 px-2.5 py-2">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-accent text-[11px] font-semibold text-accent-fg">
            {(session?.user_id ?? "?").slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium">{session?.user_id}</span>
            <span className="block font-mono text-[10px] uppercase tracking-wide text-fg-subtle">
              {session?.role}
            </span>
          </span>
          <button
            type="button"
            onClick={() => void signOut()}
            aria-label="Sign out"
            title="Sign out"
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-surface hover:text-danger"
          >
            <LogOut className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

