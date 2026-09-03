import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";

const HTTPS_MARKER = "__SUPABASE_HTTPS_ORIGIN__";
const WSS_MARKER = "__SUPABASE_WSS_ORIGIN__";

function supabaseOrigins(rawValue: string | undefined) {
  if (!rawValue) throw new Error("VITE_SUPABASE_URL is required for a production build");
  const value = new URL(rawValue);
  if (
    value.protocol !== "https:" ||
    !/^[a-z0-9-]+\.supabase\.co$/.test(value.hostname) ||
    value.port || value.username || value.password ||
    value.pathname !== "/" || value.search || value.hash
  ) {
    throw new Error("VITE_SUPABASE_URL must be an exact hosted Supabase HTTPS origin");
  }
  return { https: value.origin, wss: `wss://${value.host}` };
}

function renderSecurityHeaders(origins: ReturnType<typeof supabaseOrigins>): Plugin {
  return {
    name: "render-exact-security-headers",
    apply: "build",
    closeBundle() {
      const target = resolve(import.meta.dirname, "dist", "_headers");
      const template = readFileSync(target, "utf8");
      if (!template.includes(HTTPS_MARKER) || !template.includes(WSS_MARKER)) {
        throw new Error("Static security-header origin markers are missing");
      }
      writeFileSync(
        target,
        template.replaceAll(HTTPS_MARKER, origins.https).replaceAll(WSS_MARKER, origins.wss),
        "utf8",
      );
    },
  };
}

export default defineConfig(({ command, mode }) => {
  const env = command === "build" ? loadEnv(mode, import.meta.dirname, "VITE_") : {};
  return {
    plugins: [
      react(),
      ...(command === "build"
        ? [renderSecurityHeaders(supabaseOrigins(env.VITE_SUPABASE_URL))]
        : []),
    ],
    build: {
      sourcemap: false,
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
    },
  };
});
