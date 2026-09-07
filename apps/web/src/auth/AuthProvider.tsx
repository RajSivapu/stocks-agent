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
export const PASSWORD_RECOVERY_STORAGE_KEY = "personal-stock-agent-password-recovery";

export function isPasswordRecoveryCallback(hash: string): boolean {
  if (!hash.startsWith("#")) return false;
  return new URLSearchParams(hash.slice(1)).get("type") === "recovery";
}

function recoveryMarkerIsSet(): boolean {
  if (typeof window === "undefined") return false;
  return window.sessionStorage.getItem(PASSWORD_RECOVERY_STORAGE_KEY) === "pending";
}

function storeRecoveryMarker(active: boolean): void {
  if (typeof window === "undefined") return;
  if (active) window.sessionStorage.setItem(PASSWORD_RECOVERY_STORAGE_KEY, "pending");
  else window.sessionStorage.removeItem(PASSWORD_RECOVERY_STORAGE_KEY);
}

export interface AuthClient {
  getSession(): Promise<{ data: { session: AuthSession | null }; error: unknown }>;
  onAuthStateChange(callback: (event: string, session: AuthSession | null) => void): {
    data: { subscription: { unsubscribe(): void } };
  };
  signInWithPassword(input: { email: string; password: string }): Promise<{
    data: { session: AuthSession | null };
    error: unknown;
  }>;
  signInWithOtp(input: {
    email: string;
    options: { shouldCreateUser: false; emailRedirectTo: string };
  }): Promise<{ error: unknown }>;
  resetPasswordForEmail(email: string, options: { redirectTo: string }): Promise<{ error: unknown }>;
  updateUser(input: { password: string }): Promise<{ error: unknown }>;
  signOut(input: { scope: "global" | "local" }): Promise<{ error: unknown }>;
}

interface AuthContextValue {
  session: AuthSession | null;
  loading: boolean;
  locked: boolean;
  recovering: boolean;
  signInWithPassword(email: string, password: string): Promise<void>;
  sendSignInLink(email: string): Promise<void>;
  sendPasswordReset(email: string): Promise<void>;
  updatePassword(password: string): Promise<void>;
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
    signInWithPassword: async (input) => await client.auth.signInWithPassword(input),
    signInWithOtp: async (input) => await client.auth.signInWithOtp(input),
    resetPasswordForEmail: async (email, options) => await client.auth.resetPasswordForEmail(email, options),
    updateUser: async (input) => await client.auth.updateUser(input),
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
  if (isPasswordRecoveryCallback(window.location.hash)) storeRecoveryMarker(true);
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
  const [recovering, setRecovering] = useState(() => {
    const pending = recoveryMarkerIsSet() || isPasswordRecoveryCallback(window.location.hash);
    if (pending) storeRecoveryMarker(true);
    return pending;
  });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastActivityAt = useRef<number | null>(null);

  const setRecoveryMode = useCallback((active: boolean) => {
    storeRecoveryMarker(active);
    setRecovering(active);
  }, []);

  const lock = useCallback(() => {
    lastActivityAt.current = null;
    setSession(null);
    setLocked(true);
    setRecoveryMode(false);
    void client.signOut({ scope: "local" }).catch(() => undefined);
  }, [client, setRecoveryMode]);

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
      if (event === "PASSWORD_RECOVERY") setRecoveryMode(true);
      if (event === "SIGNED_OUT") setRecoveryMode(false);
      if (nextSession && event !== "TOKEN_REFRESHED") setLocked(false);
      setLoading(false);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [client, setRecoveryMode]);

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
    recovering,
    signInWithPassword: async (email, password) => {
      const result = await client.signInWithPassword({ email, password });
      if (result.error || !result.data.session) throw new Error("Email or password is incorrect.");
      setSession(result.data.session);
      setLocked(false);
      setRecoveryMode(false);
    },
    sendSignInLink: async (email) => {
      const result = await client.signInWithOtp({
        email,
        options: {
          shouldCreateUser: false,
          emailRedirectTo: window.location.origin,
        },
      });
      if (result.error) throw new Error("The sign-in email could not be sent.");
    },
    sendPasswordReset: async (email) => {
      const result = await client.resetPasswordForEmail(email, { redirectTo: window.location.origin });
      if (result.error) throw new Error("The password setup email could not be sent.");
    },
    updatePassword: async (password) => {
      const result = await client.updateUser({ password });
      if (result.error) throw new Error("The password could not be updated.");
      setRecoveryMode(false);
    },
    signOut: async () => {
      try {
        await client.signOut({ scope: "global" });
      } finally {
        lastActivityAt.current = null;
        setSession(null);
        setLocked(false);
        setRecoveryMode(false);
        window.sessionStorage.clear();
      }
    },
  }), [client, loading, locked, recovering, session, setRecoveryMode]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
