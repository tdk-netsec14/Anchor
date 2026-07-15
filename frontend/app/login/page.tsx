"use client";

import { GithubIcon } from "@/components/ui/GithubIcon";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Mark } from "@/components/ui/Mark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/feedback";
import { useAuth } from "@/hooks/useAuth";
import { toApiError } from "@/lib/api-client";
import { cn } from "@/lib/format";
import { Role } from "@/types/api";

const PRESETS = [
  { username: "Engineer", role: "user" as Role, note: "Ask questions, read documents and metrics." },
  { username: "Admin", role: "admin" as Role, note: "Everything above, plus ingesting and deleting documents." },
];

export default function LoginPage() {
  const { session, initialising, signingIn, signIn } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("Engineer");
  const [role, setRole] = useState<Role>("user");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!initialising && session) router.replace("/assistant");
  }, [initialising, session, router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await signIn(username.trim() || "Engineer", role);
    } catch (err) {
      setError(toApiError(err).message);
    }
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between px-6 py-5 sm:px-10">
        <Link href="/" className="flex items-center gap-2.5">
          <Mark />
          <span className="text-[15px] font-semibold tracking-tight">Anchor</span>
        </Link>
        <a
          href="https://github.com/tdk-netsec14/Anchor"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 text-[13px] text-fg-muted transition-colors hover:text-fg"
        >
          <GithubIcon className="size-4" />
          GitHub
        </a>
      </header>

      <main className="flex flex-1 items-center justify-center px-6 py-10 sm:px-10">
        <div className="w-full max-w-sm">
          <h1 className="text-[22px] font-semibold tracking-tight">Sign in to Anchor</h1>
          <p className="mt-1.5 text-[13.5px] leading-relaxed text-fg-muted">
            Choose a role to receive a signed access token. Your role decides what the
            backend will authorise you to do.
          </p>

          <form onSubmit={submit} className="mt-7 space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="username" className="text-[13px] font-medium">
                Username
              </label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                placeholder="Engineer"
              />
            </div>

            <fieldset className="space-y-1.5">
              <legend className="text-[13px] font-medium">Role</legend>
              <div className="grid gap-2">
                {PRESETS.map((preset) => {
                  const active = role === preset.role;
                  return (
                    <button
                      key={preset.role}
                      type="button"
                      onClick={() => {
                        setRole(preset.role);
                        setUsername(preset.username);
                      }}
                      className={cn(
                        "rounded-lg border px-3 py-2.5 text-left transition-colors",
                        active
                          ? "border-accent bg-accent-soft"
                          : "border-border bg-surface hover:border-border-strong",
                      )}
                    >
                      <span className="flex items-center justify-between">
                        <span className="text-[13px] font-medium capitalize">{preset.role}</span>
                        {active ? (
                          <span className="font-mono text-[10.5px] uppercase tracking-wide text-accent">
                            selected
                          </span>
                        ) : null}
                      </span>
                      <span className="mt-0.5 block text-[12px] leading-relaxed text-fg-muted">
                        {preset.note}
                      </span>
                    </button>
                  );
                })}
              </div>
            </fieldset>

            {error ? (
              <p role="alert" className="rounded-lg border border-danger/30 bg-danger-soft/60 px-3 py-2 text-[13px] text-fg">
                {error}
              </p>
            ) : null}

            <Button type="submit" variant="primary" size="lg" className="w-full" disabled={signingIn}>
              {signingIn ? <Spinner /> : null}
              {signingIn ? "Signing in…" : "Continue"}
              {!signingIn ? <ArrowRight className="size-4" /> : null}
            </Button>
          </form>

          <p className="mt-6 text-[12px] leading-relaxed text-fg-subtle">
            This build exchanges a username and role for a JWT without verifying a
            password — a deliberate simplification of the demo, documented in the
            repository README.
          </p>
        </div>
      </main>
    </div>
  );
}
