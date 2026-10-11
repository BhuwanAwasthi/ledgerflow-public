// Phase evidence: run a scenario from the launcher and capture the dashboard and live run in light and dark.
//   node scripts/screenshots.mjs [outDir] [scenarioId] [light,dark]
// Needs `make dev` running (API :8000, Vite :5173). For a mid-run shot without Azure, start the API with
// LLM_BACKEND=fake FAKE_LLM_DELAY_S=1.5.
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

const out = process.argv[2] ?? "screenshots";
const scenario = process.argv[3] ?? "S01_happy";
const themes = (process.argv[4] ?? "light,dark").split(",");
const base = process.env.APP_URL ?? "http://localhost:5173";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch();

async function page(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: theme });
  await ctx.addInitScript((t) => {
    localStorage.setItem("lf-theme", t);
    localStorage.setItem("lf-role", "reviewer");
  }, theme);
  return ctx.newPage();
}

for (const theme of themes) {
  const p = await page(theme);
  const shot = async (name, opts = {}) => {
    const path = join(out, `${name}-${theme}.png`);
    if (opts.fullPage) {
      // grow the viewport instead of Playwright's stitched full page, so the sticky sidebar renders correctly
      const h = await p.evaluate(() => document.documentElement.scrollHeight);
      await p.setViewportSize({ width: 1440, height: Math.max(900, h) });
      await p.waitForTimeout(500);
      await p.screenshot({ path });
      await p.setViewportSize({ width: 1440, height: 900 });
    } else {
      await p.screenshot({ path, ...opts });
    }
    console.log("saved", path);
  };

  // launcher -> run the scenario (this is how the demo starts a case)
  await p.goto(`${base}/scenarios`);
  await p.getByText(scenario).waitFor();
  const card = p.locator("div.rounded-lg", { has: p.getByText(scenario, { exact: true }) }).last();
  await card.getByRole("button", { name: "Run" }).click();
  await p.waitForURL(/\/cases\/\d+\/live/);

  // mid-run: wait until a few stages are done and one is running
  await p.locator("li", { hasText: "Running" }).first().waitFor({ timeout: 30_000 });
  await p.waitForFunction(() => document.querySelectorAll("ol > li").length >= 4, null, { timeout: 60_000 });
  await p.waitForTimeout(400);
  await shot("live-midrun");

  // completion: poll the API until the case leaves received/processing
  const caseId = p.url().match(/cases\/(\d+)/)[1];
  const api = process.env.API_URL ?? "http://localhost:8000";
  for (let i = 0; i < 240; i++) {
    const c = await (await fetch(`${api}/api/cases/${caseId}`)).json();
    if (!["received", "processing"].includes(c.status)) break;
    await p.waitForTimeout(500);
  }
  await p.waitForTimeout(1500);
  await shot("live-complete", { fullPage: true });

  await p.goto(`${base}/`);
  await p.getByText("Cases over time").waitFor();
  await p.waitForTimeout(2500); // chart animation
  await shot("dashboard", { fullPage: true });

  await p.goto(`${base}/submissions`);
  await p.waitForTimeout(1500);
  await shot("submissions");
  await p.context().close();
}

await browser.close();
