"use client";

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

/**
 * Create an account and its first workspace.
 *
 * The backend may have registration closed (`AUTH_ALLOW_REGISTRATION=false`),
 * which it reports as an error on submit. The form stays visible rather than
 * hiding itself behind a build-time flag the frontend has no way to read.
 */
export default function SignupPage() {
  const { session, initialising, signingIn, signUp } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!initialising && session) router.replace("/dashboard");
  }, [initialising, session, router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await signUp({
        email: email.trim(),
        password,
        full_name: fullName.trim(),
        workspace_name: workspaceName.trim() || undefined,
      });
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
        <Link href="/login" className="text-[13px] text-fg-muted transition-colors hover:text-fg">
          Sign in
        </Link>
      </header>

      <main className="flex flex-1 items-center justify-center px-6 py-10 sm:px-10">
        <div className="w-full max-w-sm">
          <h1 className="text-[22px] font-semibold tracking-tight">Create your workspace</h1>
          <p className="mt-1.5 text-[13.5px] leading-relaxed text-fg-muted">
            Documents, conversations and API keys are all scoped to a workspace. You will
            be its owner.
          </p>

          <form onSubmit={submit} className="mt-7 space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="email" className="text-[13px] font-medium">
                Email
              </label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                required
                placeholder="you@company.com"
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="password" className="text-[13px] font-medium">
                Password
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
                minLength={12}
              />
              <p className="text-[11.5px] text-fg-subtle">At least 12 characters.</p>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="full_name" className="text-[13px] font-medium">
                Name <span className="font-normal text-fg-subtle">(optional)</span>
              </label>
              <Input
                id="full_name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                autoComplete="name"
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="workspace_name" className="text-[13px] font-medium">
                Workspace name <span className="font-normal text-fg-subtle">(optional)</span>
              </label>
              <Input
                id="workspace_name"
                value={workspaceName}
                onChange={(e) => setWorkspaceName(e.target.value)}
                placeholder="Acme Support"
              />
            </div>

            {error ? (
              <p
                role="alert"
                className="rounded-lg border border-danger/30 bg-danger-soft/60 px-3 py-2 text-[13px] text-fg"
              >
                {error}
              </p>
            ) : null}

            <Button type="submit" variant="primary" size="lg" className="w-full" disabled={signingIn}>
              {signingIn ? <Spinner /> : null}
              {signingIn ? "Creating…" : "Create account"}
              {!signingIn ? <ArrowRight className="size-4" /> : null}
            </Button>
          </form>
        </div>
      </main>
    </div>
  );
}
