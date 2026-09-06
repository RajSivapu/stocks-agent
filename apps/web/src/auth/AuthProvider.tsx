import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type AuthSession = Pick<Session, "access_token" | "user" | "expires_at">;

export interface AuthClient {
  getSession(): Promise<{ data: { session: AuthSession | null }; error: unknown }>;
  onAuthStateChange(callback: (event: string, session: AuthSession | null) => void): {
    data: { subscription: { unsubscribe(): void } };
  };
  signInWithOtp(input: {
    email: string;
    options: { shouldCreateUser: false; emailRedirectTo: string };
  }): Promise<{ error: unknown }>;
  verifyOtp(input: { email: string; token: string; type: "email" }): Promise<{
    data: { session: AuthSession | null };
    error: unknown;
  }>;
  signOut(input: { scope: "global" | "local" }): Promise<{ error: unknown }>;
}

interface AuthContextValue {
  session: AuthSession | null;
  loading: boolean;
  locked: boolean;
  sendOtp(email: string): Promise<void>;
  verifyOtp(email: string, token: string): Promise<void>;
  signOut(): Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const BROWSER_AUTH_FLOW = {
  persistSession: true,
  autoRefreshToken: true,
  detectSessionInUrl: true,
  flowType: "implicit" as const,
};

function adapter(client: SupabaseClient): AuthClient {
  return {
    getSession: async () => await client.auth.getSession(),
    onAuthStateChange: (callback) => client.auth.onAuthStateChange((event, session) => callback(event, session)),
    signInWithOtp: async (input) => await client.auth.signInWithOtp(input),
    verifyOtp: async (input) => await client.auth.verifyOtp(input),
    signOut: async (input) => await client.auth.signOut(input),
  };
}

export function createBrowserAuthClient(): AuthClient {
  const url = import.meta.env.VITE_SUPABASE_URL?.trim();
  const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim();
  if (!url || !key) throw new Error("Public authentication configuration is unavailable.");
  const parsed = new URL(url);
  if (parsed.protocol !== "https:" || !parsed.hostname.endsWith(".supabase.co") || parsed.origin !== url) {
    throw new Error("Public authentication configuration is invalid.");
  }
  return adapter(createClient(url, key, {
    auth: {
      storage: window.sessionStorage,
      ...BROWSER_AUTH_FLOW,
    },
  }));
}

export function AuthProvider({
  client,
  inactivityMs = 30 * 60 * 1_000,
  children,
}: PropsWithChildren<{ client: AuthClient; inactivityMs?: number }>) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [locked, setLocked] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastActivityAt = useRef<number | null>(null);

  const lock = useCallback(() => {
    lastActivityAt.current = null;
    setSession(null);
    setLocked(true);
    void client.signOut({ scope: "local" }).catch(() => undefined);
  }, [client]);

  useEffect(() => {
    let active = true;
    void client.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    }).catch(() => {
      if (active) setSession(null);
    }).finally(() => {
      if (active) setLoading(false);
    });
    const { data } = client.onAuthStateChange((event, nextSession) => {
      if (!active) return;
      setSession(nextSession);
      if (nextSession && event !== "TOKEN_REFRESHED") setLocked(false);
      setLoading(false);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [client]);

  useEffect(() => {
    if (!session) {
      lastActivityAt.current = null;
      return;
    }
    if (lastActivityAt.current === null) lastActivityAt.current = Date.now();

    const scheduleLock = () => {
      if (timer.current) clearTimeout(timer.current);
      const remaining = inactivityMs - (Date.now() - (lastActivityAt.current ?? Date.now()));
      if (remaining <= 0) {
        lock();
        return;
      }
      timer.current = setTimeout(lock, remaining);
    };
    const recordActivity = () => {
      lastActivityAt.current = Date.now();
      scheduleLock();
    };
    const events = ["pointerdown", "keydown", "focus"] as const;
    for (const event of events) window.addEventListener(event, recordActivity, { passive: true });
    scheduleLock();
    return () => {
      if (timer.current) clearTimeout(timer.current);
      for (const event of events) window.removeEventListener(event, recordActivity);
    };
  }, [inactivityMs, lock, session]);

  const value = useMemo<AuthContextValue>(() => ({
    session,
    loading,
    locked,
    sendOtp: async (email) => {
      const result = await client.signInWithOtp({
        email,
        options: {
          shouldCreateUser: false,
          emailRedirectTo: window.location.origin,
        },
      });
      if (result.error) throw new Error("The sign-in email could not be sent.");
    },
    verifyOtp: async (email, token) => {
      const result = await client.verifyOtp({ email, token, type: "email" });
      if (result.error || !result.data.session) throw new Error("That code is invalid or expired.");
      setSession(result.data.session);
      setLocked(false);
    },
    signOut: async () => {
      try {
        await client.signOut({ scope: "global" });
      } finally {
        lastActivityAt.current = null;
        setSession(null);
        setLocked(false);
        window.sessionStorage.clear();
      }
    },
  }), [client, loading, locked, session]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
