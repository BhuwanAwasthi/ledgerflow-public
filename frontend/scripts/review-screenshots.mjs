// Phase F2 evidence: the review workspace through the S02b remark loop and the S04 duplicate compare view.
//   node scripts/review-screenshots.mjs [outDir] [light|dark] [1440x900|1280x720] [full|uncertain]
// `uncertain` only runs S01 and shoots its workspace: point the API's LLM_FIXTURES_DIR at fixtures whose S01
// classifier confidence is below document_types min_confidence to see the classification callout.
// Needs a freshly seeded API (S01 must be a first-time run) and Vite: APP_URL (default :5173), API_URL (default :8000).
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

const out = process.argv[2] ?? "screenshots";
const theme = process.argv[3] ?? "light";
const [vw, vh] = (process.argv[4] ?? "1440x900").split("x").map(Number);
const mode = process.argv[5] ?? "full";
const base = process.env.APP_URL ?? "http://localhost:5173";
const api = process.env.API_URL ?? "http://localhost:8000";
mkdirSync(out, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getCase = async (id) => (await fetch(`${api}/api/cases/${id}`)).json();
async function settle(id, statuses = ["needs_review", "on_hold", "approved", "exported", "rejected", "failed"]) {
  for (let i = 0; i < 240; i++) {
    const c = await getCase(id);
    if (statuses.includes(c.status)) return c;
    await sleep(500);
  }
  throw new Error(`case ${id} did not settle`);
}
async function runScenario(id) {
  const r = await fetch(`${api}/api/fixtures/${id}/run`, { method: "POST" });
  if (!r.ok) throw new Error(`${id}: ${r.status} ${await r.text()}`);
  const c = await r.json();
  return settle(c.id);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, deviceScaleFactor: 1, colorScheme: theme });
await ctx.addInitScript((t) => {
  localStorage.setItem("lf-theme", t);
  localStorage.setItem("lf-role", "reviewer");
}, theme);
const p = await ctx.newPage();
const shot = async (name, { keepPointer = false } = {}) => {
  // toasts would cover the grid: close them first (not while a dialog is open: a click outside it would close it)
  if (!(await p.locator("[role=dialog]").count())) {
    for (const b of await p.locator("[data-sonner-toast] [data-close-button]").all()) await b.click({ force: true }).catch(() => undefined);
    await p.waitForFunction(() => !document.querySelector("[data-sonner-toast]"), null, { timeout: 8_000 }).catch(() => undefined);
  }
  if (!keepPointer) await p.mouse.move(2, vh - 2).catch(() => undefined); // park the pointer where it highlights nothing
  await p.waitForTimeout(500);
  const path = join(out, `${name}-${theme}-${vw}x${vh}.png`);
  await p.screenshot({ path });
  console.log("saved", path);
};
const canvases = async (n = 1) => p.waitForFunction((k) => document.querySelectorAll(".react-pdf__Page canvas").length >= k, n, { timeout: 30_000 });

if (mode === "uncertain") {
  const c = await runScenario("S01_happy");
  console.log("case", c.ref, c.status, c.decision);
  await p.goto(`${base}/cases/${c.id}/review`);
  await p.getByRole("alert").first().waitFor();
  await canvases();
  await p.waitForTimeout(800);
  await shot("uncertain-classification");
  await browser.close();
  process.exit(0);
}

const fixtures = await (await fetch(`${api}/api/fixtures`)).json();
const remark = fixtures.find((f) => f.id === "S02b_split_part2").demo_remark;
const s01 = await runScenario("S01_happy");
await runScenario("S02a_split_part1");
const s02b = await runScenario("S02b_split_part2");
const s04 = await runScenario("S04_duplicate_scan");
console.log("cases", { s01: s01.ref, s02b: `${s02b.ref} ${s02b.status}`, s04: `${s04.ref} ${s04.status}` });

// 1. S02b before the remark: amber cells, PO balance over, hovering the invoice number highlights it on the PDF
await p.goto(`${base}/cases/${s02b.id}/review`);
await p.getByRole("radio", { name: "v1" }).waitFor();
await canvases();
await p.waitForTimeout(800);
await shot("s02b-1-before-remark");
// hovering a grid cell highlights the same value on the PDF
await p.locator("td", { hasText: "BL-0107" }).first().hover();
await p.waitForTimeout(400);
await shot("s02b-1a-hover-link", { keepPointer: true });
await p.getByRole("tab", { name: /Rules/ }).click();
await p.waitForTimeout(300);
await shot("s02b-1b-rules");
await p.getByRole("tab", { name: /Match/ }).click();
await p.waitForTimeout(300);
await shot("s02b-1c-match");

// 2. remark (R) -> reconciler -> policy passes -> sticky review -> template v2 with the diff
await p.getByRole("tab", { name: /Template/ }).click();
await p.mouse.move(5, 5);
await p.keyboard.press("r");
// typing the shortcut letters in the remark box must not trigger Approve / Reject / Ask vendor
await p.getByLabel("Remark").focus();
await p.keyboard.type("arxv AXRV");
const dialogs = await p.locator("[role=dialog]").count();
const title = await p.locator("[role=dialog] h2").first().innerText();
const typed = await p.getByLabel("Remark").inputValue();
if (dialogs !== 1 || title !== "Request changes" || typed !== "arxv AXRV") {
  throw new Error(`shortcut leaked while typing: dialogs=${dialogs} title=${title} text=${typed}`);
}
console.log("shortcut check OK: typing a/r/x/v in the remark box opened nothing else");
await p.getByLabel("Remark").fill(remark);
await p.waitForTimeout(200);
await shot("s02b-2a-remark-dialog");
await p.keyboard.press("Control+Enter");
await p.getByRole("radio", { name: "v2" }).waitFor({ timeout: 60_000 });
await settle(s02b.id, ["needs_review"]);
await p.waitForTimeout(1500);
await shot("s02b-2-after-v2-diff");
await p.getByRole("tab", { name: /History/ }).click();
await p.waitForTimeout(400);
await shot("s02b-2b-history");

// 3. approve (A, then confirm)
await p.getByRole("tab", { name: /Template/ }).click();
await p.mouse.move(5, 5);
await p.keyboard.press("a");
await p.getByRole("button", { name: "Approve", exact: true }).last().click();
await settle(s02b.id, ["approved"]);
await p.waitForTimeout(2500);
await shot("s02b-3-approved");

// 4. S04: a scanned duplicate of S01 opens the side-by-side compare
await p.goto(`${base}/cases/${s04.id}/review`);
await p.getByText("Possible double payment").waitFor();
await canvases(2);
await p.waitForTimeout(1000);
await shot("s04-compare");

// 5. S07 (non-PO, in review): an inline grid edit becomes a set_field op (no model), then Ask vendor (V)
const s07 = await runScenario("S07_non_po");
await p.goto(`${base}/cases/${s07.id}/review`);
await p.getByRole("radio", { name: "v1" }).waitFor();
await canvases();
const terms = p.locator("td").filter({ hasText: /^Net \d+$/ }).first();
await terms.click();
await p.keyboard.press("Control+a");
await p.keyboard.type("Net 45");
await p.keyboard.press("Enter");
await p.waitForTimeout(300);
await shot("s07-1-grid-edit-pending");
await p.keyboard.press("r");
await p.getByRole("button", { name: "Send to reconciler" }).click();
await p.getByRole("radio", { name: "v2" }).waitFor({ timeout: 60_000 });
await settle(s07.id, ["needs_review"]);
await p.waitForTimeout(1200);
await shot("s07-2-grid-edit-v2");
await p.mouse.move(5, 5);
await p.keyboard.press("v");
await p.getByLabel("What do you need?").fill("Please send the purchase approval from your budget owner for this facilities work.");
await p.getByRole("button", { name: "Put on hold and draft" }).click();
await p.getByRole("link", { name: "Open in mail app" }).waitFor({ timeout: 60_000 });
await p.waitForTimeout(500);
await shot("s07-3-ask-vendor-draft");

await browser.close();
