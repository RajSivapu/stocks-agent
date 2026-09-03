import { createClient } from "@supabase/supabase-js";

type PublicEnvironment = {
  VITE_SUPABASE_URL?: string;
  VITE_SUPABASE_PUBLISHABLE_KEY?: string;
  DEV?: boolean;
};

function exactSupabaseUrl(rawValue: string | undefined, allowLocalhost: boolean): string {
  if (!rawValue) throw new Error("PUBLIC_CONFIG_UNAVAILABLE");
  let value: URL;
  try {
    value = new URL(rawValue);
  } catch {
    throw new Error("PUBLIC_CONFIG_UNAVAILABLE");
  }
  const hosted = value.protocol === "https:" &&
    /^[a-z0-9-]+\.supabase\.co$/.test(value.hostname);
  const local = allowLocalhost && value.protocol === "http:" &&
    ["localhost", "127.0.0.1"].includes(value.hostname);
  if (
    (!hosted && !local) || value.username || value.password || value.search || value.hash ||
    (value.pathname !== "/" && value.pathname !== "")
  ) {
    throw new Error("PUBLIC_CONFIG_UNAVAILABLE");
  }
  return value.origin;
}

function publishableKey(rawValue: string | undefined): string {
  const value = rawValue?.trim() ?? "";
  if (value.length < 20 || value.length > 4096 || /\s/.test(value)) {
    throw new Error("PUBLIC_CONFIG_UNAVAILABLE");
  }
  return value;
}

export function createBrowserSupabaseClient(
  environment: PublicEnvironment = import.meta.env,
) {
  return createClient(
    exactSupabaseUrl(environment.VITE_SUPABASE_URL, environment.DEV === true),
    publishableKey(environment.VITE_SUPABASE_PUBLISHABLE_KEY),
    {
      auth: {
        flowType: "pkce",
        detectSessionInUrl: false,
        persistSession: true,
        autoRefreshToken: true,
      },
    },
  );
}
