import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin, type PluginOption } from "vite";
import { configDefaults } from "vitest/config";

const SUPABASE_HTTPS_MARKER = "__SUPABASE_HTTPS_ORIGIN__";
const SUPABASE_WSS_MARKER = "__SUPABASE_WSS_ORIGIN__";
const API_MARKER = "__DASHBOARD_API_ORIGIN__";

function canonicalSupabaseOrigin(raw: string | undefined): URL {
  if (!raw) throw new Error("VITE_SUPABASE_URL is required for a production build");
  const value = new URL(raw);
  if (value.protocol !== "https:" || !/^[a-z0-9-]+\.supabase\.co$/.test(value.hostname) ||
      value.port || value.username || value.password || value.pathname !== "/" ||
      value.search || value.hash) {
    throw new Error("VITE_SUPABASE_URL must be an exact hosted Supabase HTTPS origin");
  }
  return value;
}

function canonicalApiOrigin(raw: string | undefined): URL {
  if (!raw) throw new Error("VITE_DASHBOARD_API_URL is required for a production build");
  const value = new URL(raw);
  if (value.protocol !== "https:" || value.port || value.username || value.password ||
      value.search || value.hash || value.pathname !== "/functions/v1/owner-dashboard-api") {
    throw new Error("VITE_DASHBOARD_API_URL must be the exact HTTPS owner dashboard function URL");
  }
  return value;
}

function renderSecurityHeaders(supabase: URL, api: URL): Plugin {
  return {
    name: "render-owner-dashboard-security-headers",
    apply: "build",
    closeBundle() {
      const target = resolve(import.meta.dirname, "dist", "_headers");
      const template = readFileSync(target, "utf8");
      for (const marker of [SUPABASE_HTTPS_MARKER, SUPABASE_WSS_MARKER, API_MARKER]) {
        if (!template.includes(marker)) throw new Error(`missing security-header marker ${marker}`);
      }
      writeFileSync(
        target,
        template
          .replaceAll(SUPABASE_HTTPS_MARKER, supabase.origin)
          .replaceAll(SUPABASE_WSS_MARKER, `wss://${supabase.host}`)
          .replaceAll(API_MARKER, api.origin),
        "utf8",
      );
    },
  };
}

export default defineConfig(({ command, mode }) => {
  const env = command === "build" ? loadEnv(mode, import.meta.dirname, "VITE_") : {};
  const plugins: PluginOption[] = [...react()];
  if (command === "build") {
    plugins.push(renderSecurityHeaders(
      canonicalSupabaseOrigin(env.VITE_SUPABASE_URL),
      canonicalApiOrigin(env.VITE_DASHBOARD_API_URL),
    ));
  }
  return {
    plugins,
    build: { sourcemap: false },
    test: {
      environment: "jsdom",
      exclude: [...configDefaults.exclude, "e2e/**"],
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
    },
  };
});
