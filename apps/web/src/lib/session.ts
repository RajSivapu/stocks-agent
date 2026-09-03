import type { SupabaseClient } from "@supabase/supabase-js";

export const CURRENT_CONSENT_VERSION = "provider-data-v1";

export type ViewerState =
  | { kind: "signed-out"; reason?: "SESSION_REVOKED" }
  | {
    kind: "consent-required";
    userId: string;
    email: string;
    displayName: string;
  }
  | {
    kind: "ready";
    userId: string;
    email: string;
    displayName: string;
  }
  | { kind: "unavailable"; code: "PROFILE_UNAVAILABLE" };

export interface SessionService {
  loadViewer(): Promise<ViewerState>;
  requestOtp(email: string): Promise<void>;
  requestDesktopLink(email: string, redirectTo: string): Promise<void>;
  verifyOtp(email: string, token: string): Promise<void>;
  signOut(): Promise<void>;
  subscribe(listener: () => void): () => void;
}

type QueryResult<T> = Promise<{ data: T; error: unknown }>;
type ProfileRow = { display_name: string | null; status: string };
type ConsentRow = { document_version: string; accepted_at: string };
type ApiReader = {
  schema(name: "api"): {
    from(name: "profile"): {
      select(columns: string): { maybeSingle(): QueryResult<ProfileRow | null> };
    };
    from(name: "consents"): {
      select(columns: string): {
        order(column: "accepted_at", options: { ascending: false }): QueryResult<ConsentRow[]>;
      };
    };
  };
};

function safeDisplayName(value: string | null, email: string): string {
  const cleaned = value?.trim();
  return cleaned && cleaned.length <= 100 ? cleaned : email.split("@")[0] ?? "Investor";
}

export function canonicalEmail(value: string): string {
  const email = value.trim().toLowerCase();
  if (
    email.length < 3 || email.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  ) {
    throw new Error("INVALID_EMAIL");
  }
  return email;
}

function exactOtp(value: string): string {
  if (!/^\d{6}$/.test(value)) throw new Error("INVALID_OTP");
  return value;
}

export async function loadVerifiedViewer(
  client: SupabaseClient,
): Promise<ViewerState> {
  const verified = await client.auth.getUser();
  const user = verified.data.user;
  if (verified.error || !user || !user.email) {
    await client.auth.signOut({ scope: "local" });
    return { kind: "signed-out", reason: "SESSION_REVOKED" };
  }

  const reader = client as unknown as ApiReader;
  const profileResult = await reader.schema("api").from("profile")
    .select("display_name,status").maybeSingle();
  if (
    profileResult.error || !profileResult.data ||
    !["invited", "active"].includes(profileResult.data.status)
  ) {
    return { kind: "unavailable", code: "PROFILE_UNAVAILABLE" };
  }
  const consentResult = await reader.schema("api").from("consents")
    .select("document_version,accepted_at")
    .order("accepted_at", { ascending: false });
  if (consentResult.error || !Array.isArray(consentResult.data)) {
    return { kind: "unavailable", code: "PROFILE_UNAVAILABLE" };
  }
  const identity = {
    userId: user.id,
    email: user.email,
    displayName: safeDisplayName(profileResult.data.display_name, user.email),
  };
  return consentResult.data.some((row) =>
      row.document_version === CURRENT_CONSENT_VERSION &&
      typeof row.accepted_at === "string"
    )
    ? { kind: "ready", ...identity }
    : { kind: "consent-required", ...identity };
}

function checkedRedirect(value: string, applicationOrigin: string): string {
  let redirect: URL;
  try {
    redirect = new URL(value);
  } catch {
    throw new Error("INVALID_REDIRECT");
  }
  if (
    redirect.origin !== applicationOrigin || redirect.pathname !== "/auth/callback" ||
    redirect.search || redirect.hash
  ) {
    throw new Error("INVALID_REDIRECT");
  }
  return redirect.toString();
}

export function createSessionService(
  client: SupabaseClient,
  applicationOrigin = window.location.origin,
): SessionService {
  return {
    loadViewer: () => loadVerifiedViewer(client),
    async requestOtp(email) {
      const result = await client.auth.signInWithOtp({
        email: canonicalEmail(email),
        options: { shouldCreateUser: false },
      });
      if (result.error) throw new Error("OTP_REQUEST_UNAVAILABLE");
    },
    async requestDesktopLink(email, redirectTo) {
      const result = await client.auth.signInWithOtp({
        email: canonicalEmail(email),
        options: {
          shouldCreateUser: false,
          emailRedirectTo: checkedRedirect(redirectTo, applicationOrigin),
        },
      });
      if (result.error) throw new Error("OTP_REQUEST_UNAVAILABLE");
    },
    async verifyOtp(email, token) {
      const result = await client.auth.verifyOtp({
        email: canonicalEmail(email),
        token: exactOtp(token),
        type: "email",
      });
      if (result.error) throw new Error("OTP_VERIFY_UNAVAILABLE");
    },
    async signOut() {
      const result = await client.auth.signOut();
      if (result.error) throw new Error("SIGN_OUT_UNAVAILABLE");
    },
    subscribe(listener) {
      const { data } = client.auth.onAuthStateChange(() => { listener(); });
      return () => { data.subscription.unsubscribe(); };
    },
  };
}

type BrowserHistory = {
  replaceState(data: unknown, unused: string, url?: string | URL | null): void;
};

export async function consumeAuthCallback(
  client: SupabaseClient,
  location: URL,
  history: BrowserHistory,
): Promise<boolean> {
  const code = location.searchParams.get("code");
  const hasAuthMaterial = code !== null || location.hash.length > 0 ||
    ["error", "error_description", "access_token", "refresh_token", "token_hash"]
      .some((name) => location.searchParams.has(name));
  if (!hasAuthMaterial) return false;

  // Clear all query and fragment material before any network request or render can expose it.
  history.replaceState({}, "", "/");
  if (!code || code.length > 2048 || /\s/.test(code)) {
    throw new Error("AUTH_CALLBACK_UNAVAILABLE");
  }
  const result = await client.auth.exchangeCodeForSession(code);
  if (result.error) throw new Error("AUTH_CALLBACK_UNAVAILABLE");
  return true;
}
