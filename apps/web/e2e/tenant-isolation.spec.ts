import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

type Owner = { id: string; access_token: string };
type Bundle = { version: 1; supabase_url: string; owner_a: Owner; owner_b: Owner };

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`missing live E2E setting: ${name}`);
  return value;
}

test("@live both staging owners are isolated through browser-origin PostgREST and Edge calls", async ({ page }) => {
  const bundle = JSON.parse(readFileSync(required("E2E_SECURITY_USERS_FILE"), "utf8")) as Bundle;
  const projectUrl = required("E2E_SUPABASE_URL").replace(/\/$/, "");
  const publishableKey = required("E2E_SUPABASE_PUBLISHABLE_KEY");
  const webOrigin = new URL(required("E2E_BASE_URL")).origin;
  expect(bundle.version).toBe(1);
  expect(bundle.supabase_url).toBe(projectUrl);
  await page.goto(webOrigin);

  const browserFetch = async (
    url: string,
    init: { method?: string; headers: Record<string, string>; body?: string },
  ) => page.evaluate(async ({ requestUrl, requestInit }) => {
    const fetchInit: RequestInit = {
      headers: requestInit.headers,
      cache: "no-store",
      credentials: "omit",
    };
    if (requestInit.method !== undefined) fetchInit.method = requestInit.method;
    if (requestInit.body !== undefined) fetchInit.body = requestInit.body;
    const response = await fetch(requestUrl, fetchInit);
    const text = await response.text();
    return { status: response.status, ok: response.ok, body: JSON.parse(text) as unknown };
  }, { requestUrl: url, requestInit: init });

  const ownerPairs: ReadonlyArray<readonly [Owner, Owner]> = [
    [bundle.owner_a, bundle.owner_b],
    [bundle.owner_b, bundle.owner_a],
  ];
  for (const [owner, other] of ownerPairs) {
    const headers = {
      apikey: publishableKey,
      authorization: `Bearer ${owner.access_token}`,
      "accept-profile": "api",
    };
    const own = await browserFetch(`${projectUrl}/rest/v1/profile?select=id`, { headers });
    expect(own.ok).toBeTruthy();
    expect(own.body).toEqual([{ id: owner.id }]);

    const cross = await browserFetch(
      `${projectUrl}/rest/v1/profile?select=id&id=eq.${encodeURIComponent(other.id)}`,
      { headers },
    );
    expect(cross.ok).toBeTruthy();
    expect(cross.body).toEqual([]);

    const forgedMutation = await browserFetch(
      `${projectUrl}/functions/v1/app-api/portfolio/preview`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${owner.access_token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ owner_id: other.id }),
      },
    );
    expect(forgedMutation.status).toBe(400);
  }

  const anonymous = await browserFetch(`${projectUrl}/rest/v1/profile?select=id`, {
    headers: { apikey: publishableKey, "accept-profile": "api" },
  });
  expect(anonymous.ok).toBeTruthy();
  expect(anonymous.body).toEqual([]);
});
