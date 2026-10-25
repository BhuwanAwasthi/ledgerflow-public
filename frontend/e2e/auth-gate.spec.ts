/**
 * N2 login wall, frontend half: signed out, every route in the router redirects to /login?next=<path>,
 * except the public shell. The route list is the one the router is built from (src/lib/routes.ts).
 */
import { expect, test, type Page } from "@playwright/test";
import { fillPath, PRIVATE_ROUTES, PUBLIC_ROUTES, REDIRECTS } from "../src/lib/routes";

const SAMPLE_PARAMS = { ref: "INV-2026-0003", id: "7", token: "tok_abc123", tab: "rules" };

function concrete(pattern: string): string {
  return pattern === "*" ? "/no-such-screen/at-all" : fillPath(pattern, SAMPLE_PARAMS);
}

/** Every request the app makes to the API while signed out (there should be none to private data). */
function watchApi(page: Page): string[] {
  const calls: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith("/api/") || u.pathname.startsWith("/portal/")) {
      if (u.port !== "4173") calls.push(`${r.method()} ${u.pathname}`);
    }
  });
  return calls;
}

const privatePaths = [...Object.values(PRIVATE_ROUTES), ...Object.keys(REDIRECTS)].map(concrete);

for (const path of privatePaths) {
  test(`signed out: ${path} -> /login?next=${path}`, async ({ page }) => {
    const calls = watchApi(page);
    const deepLink = `${path}?tab=x#frag`;
    await page.goto(deepLink);
    await expect(page).toHaveURL(/\/login\?next=/);
    const next = new URL(page.url()).searchParams.get("next");
    expect(next).toBe(deepLink); // the deep link, query and hash included, survives the redirect
    await expect(page.getByRole("heading", { name: "Sign in to LedgerFlow" })).toBeVisible();
    expect(calls).toEqual([]);
  });
}

for (const path of Object.values(PUBLIC_ROUTES)) {
  test(`signed out: public ${path} stays put`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    expect(new URL(page.url()).pathname).toBe(path);
  });
}

test("the production bundle has no dev sign-in", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in to LedgerFlow" })).toBeVisible();
  await expect(page.getByTestId("dev-sign-in")).toHaveCount(0);
});

test("an unsafe next is shown nowhere and never followed", async ({ page }) => {
  await page.goto("/login?next=" + encodeURIComponent("https://evil.example/phish"));
  await expect(page.getByRole("heading", { name: "Sign in to LedgerFlow" })).toBeVisible();
  await expect(page.getByText("evil.example")).toHaveCount(0);
});
