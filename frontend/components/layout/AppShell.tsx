"use client";

import { Menu } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Sidebar } from "@/components/layout/Sidebar";
import { Spinner } from "@/components/ui/feedback";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { useAuth } from "@/hooks/useAuth";

/**
 * The signed-in frame: an auth gate, the workspace sidebar, and a top bar.
 *
 * The gate is a redirect, not a render of a blank page — a deep link into a
 * protected route with no session should land on /login with a reason, the
 * same as any other server.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { session, initialising } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (!initialising && !session) {
      router.replace("/login");
    }
  }, [initialising, session, router]);

  if (initialising) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-fg-subtle">
        <Spinner className="size-5" />
      </div>
    );
  }

  if (!session) return null;

  return (
    <div className="flex min-h-dvh bg-bg">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 lg:block">
        <Sidebar />
      </aside>

      {drawerOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute inset-y-0 left-0 w-64 shadow-xl">
            <Sidebar onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex h-14 items-center gap-2 border-b border-border bg-bg/85 px-4 backdrop-blur">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open navigation"
            className="inline-flex size-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg lg:hidden"
          >
            <Menu className="size-4" />
          </button>
          <div className="flex-1" />
          <ThemeToggle />
        </header>

        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
