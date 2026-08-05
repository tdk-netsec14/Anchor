"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { SESSION_EXPIRED, api } from "@/lib/api-client";
import { Session } from "@/types/api";

type AuthValue = {
  session: Session | null;
  /** True until the initial session probe resolves; gates the shell. */
  initialising: boolean;
  signingIn: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: {
    email: string;
    password: string;
    full_name?: string;
    workspace_name?: string;
  }) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [initialising, setInitialising] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const router = useRouter();

  useEffect(() => {
    let active = true;
    api
      .session()
      .then((next) => {
        if (active) setSession(next);
      })
      .catch(() => {
        if (active) setSession(null);
      })
      .finally(() => {
        if (active) setInitialising(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // The proxy raises this when FastAPI rejects the token, so a session that
  // expires mid-use drops to the login screen instead of failing silently.
  useEffect(() => {
    const onExpired = () => {
      setSession(null);
      router.push("/login");
    };
    window.addEventListener(SESSION_EXPIRED, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED, onExpired);
  }, [router]);

  const signIn = useCallback(async (email: string, password: string) => {
    setSigningIn(true);
    try {
      setSession(await api.login(email, password));
      router.push("/dashboard");
    } finally {
      setSigningIn(false);
    }
  }, [router]);

  const signUp = useCallback<AuthValue["signUp"]>(async (input) => {
    setSigningIn(true);
    try {
      setSession(await api.register(input));
      router.push("/dashboard");
    } finally {
      setSigningIn(false);
    }
  }, [router]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setSession(null);
      router.push("/login");
    }
  }, [router]);

  const value = useMemo(
    () => ({ session, initialising, signingIn, signIn, signUp, signOut }),
    [session, initialising, signingIn, signIn, signUp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
