import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app";
import { createCommandClient } from "./lib/app-api";
import { createDashboardRepository } from "./lib/dashboard";
import { consumeAuthCallback, createSessionService } from "./lib/session";
import { createBrowserSupabaseClient, supabaseProjectUrl } from "./lib/supabase";

const rootNode = document.getElementById("root");
if (!rootNode) throw new Error("APPLICATION_ROOT_MISSING");
const root = createRoot(rootNode);

function renderStartupFailure() {
  root.render(
    <main className="loading-screen">
      <p role="alert">Secure sign-in is temporarily unavailable. Please try again later.</p>
    </main>,
  );
}

async function bootstrap() {
  try {
    const client = createBrowserSupabaseClient();
    await consumeAuthCallback(client, new URL(window.location.href), window.history);
    const repository = createDashboardRepository(client);
    const commands = createCommandClient(
      client,
      supabaseProjectUrl(),
      (commandId) => repository.lookupCommand(commandId),
    );
    root.render(
      <StrictMode>
        <App session={createSessionService(client)} repository={repository} commands={commands} />
      </StrictMode>,
    );
  } catch {
    window.history.replaceState({}, "", "/");
    renderStartupFailure();
  }
}

void bootstrap();
