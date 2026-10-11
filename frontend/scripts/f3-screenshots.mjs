// Phase F3 evidence: approved library, vendors (+ portal upload), purchase orders, Agent Studio, launcher, and the
// done-check: price_tolerance.pct -> 5 in the Studio rules table makes a new S08 run auto-approve (then reset).
//   node scripts/f3-screenshots.mjs [outDir] [full|tour] [light|dark] [1440x900]
// `full` needs a freshly seeded API (LLM_BACKEND=fake) and changes its data; `tour` only visits the screens.
// APP_URL (default :5173), API_URL (default :8000), FIXTURES (default ../data/fixtures).
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { chromium } from "playwright";

const out = process.argv[2] ?? "screenshots";
const mode = process.argv[3] ?? "full";
const theme = process.argv[4] ?? "light";
const [vw, vh] = (process.argv[5] ?? "1440x900").split("x").map(Number);
const base = process.env.APP_URL ?? "http://localhost:5173";
const api = process.env.API_URL ?? "http://localhost:8000";
const fixtures = resolve(process.env.FIXTURES ?? "../data/fixtures");
mkdirSync(out, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (path) => (await fetch(`${api}${path}`)).json();
const SETTLED = ["needs_review", "on_hold", "approved", "exported", "rejected", "failed", "split"];
async function settle(id) {
  for (let i = 0; i < 240; i++) {
    const c = await get(`/api/cases/${id}`);
    if (SETTLED.includes(c.status)) return c;
    await sleep(500);
  }
  throw new Error(`case ${id} did not settle`);
}
async function runScenario(id) {
  const r = await fetch(`${api}/api/fixtures/${id}/run`, { method: "POST" });
  if (!r.ok) throw new Error(`${id}: ${r.status} ${await r.text()}`);
  return settle((await r.json()).id);
}
const POLICY_NAME = process.env.POLICY_NAME ?? "Policy";
const pct = async () => (await get("/api/config")).rules.rules.find((r) => r.id === "price_tolerance").params.pct;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, deviceScaleFactor: 1, colorScheme: theme, acceptDownloads: true });
await ctx.addInitScript((t) => {
  localStorage.setItem("lf-theme", t);
  localStorage.setItem("lf-role", "admin");
}, theme);
const p = await ctx.newPage();
const shot = async (name) => {
  if (!(await p.locator("[role=dialog]").count())) {
    for (const b of await p.locator("[data-sonner-toast] [data-close-button]").all()) await b.click({ force: true }).catch(() => undefined);
    await p.waitForFunction(() => !document.querySelector("[data-sonner-toast]"), null, { timeout: 8_000 }).catch(() => undefined);
  }
  await p.mouse.move(2, vh - 2).catch(() => undefined);
  await p.waitForTimeout(500);
  const path = join(out, `${name}-${theme}.png`);
  await p.screenshot({ path });
  console.log("saved", path);
};
const settled = async () => {
  await p.waitForLoadState("networkidle").catch(() => undefined);
  await p.waitForFunction(() => !document.querySelector(".skeleton"), null, { timeout: 15_000 }).catch(() => undefined);
};
const visit = async (path) => {
  await p.goto(`${base}${path}`);
  await settled();
};

if (mode === "full") {
  const s01 = await runScenario("S01_happy");
  const s02a = await runScenario("S02a_split_part1");
  const s05 = await runScenario("S05_unknown_vendor");
  const s08 = await runScenario("S08_price_variance");
  console.log("cases", { s01: s01.status, s02a: s02a.status, s05: s05.status, s08: `${s08.ref} ${s08.decision}` });
  if (s08.decision !== "needs_review") throw new Error(`S08 baseline should be needs_review, got ${s08.decision}`);

  // ---- approved library: select both, export a batch, then re-export -> confirmation
  await visit("/library");
  await p.getByRole("checkbox", { name: "Select all" }).check();
  await shot("library-1-selected");
  const dl = p.waitForEvent("download");
  await p.getByRole("button", { name: "Export batch" }).click();
  console.log("batch file:", (await dl).suggestedFilename());
  await p.getByRole("radio", { name: "All" }).click();
  await settled();
  await p.getByRole("checkbox", { name: "Select all" }).check();
  await p.getByRole("button", { name: "Download selected (ZIP)" }).click();
  await p.getByRole("dialog").waitFor();
  await shot("library-2-reexport-confirm");
  const zip = p.waitForEvent("download");
  await p.getByRole("button", { name: "Export again" }).click();
  console.log("zip file:", (await zip).suggestedFilename());
  await p.getByRole("tab", { name: "Export history" }).click();
  await settled();
  await shot("library-3-history");

  // ---- vendor portal: S05 asks the new vendor for a W-9 and a bank letter
  const journey = await get(`/api/cases/${s05.id}/journey`);
  const token = journey.portal_url.split("/portal/")[1];
  await visit(`/portal/${token}`);
  await shot("portal-1-request");
  await p.locator("input[type=file]").setInputFiles([join(fixtures, "S05_novaprint_W9.pdf"), join(fixtures, "S05_novaprint_bank_letter.pdf")]);
  await p.getByRole("button", { name: /Upload 2 file/ }).click();
  await p.getByText(/Thank you, we received/).waitFor({ timeout: 60_000 });
  await shot("portal-2-uploaded");

  // ---- vendors
  const vendor = (await get("/api/vendors")).find((v) => v.status === "pending_docs");
  await visit("/vendors");
  await shot("vendors-1-list");
  await visit(`/vendors/${vendor.id}`);
  await shot("vendors-2-detail-checks");

  // ---- purchase orders
  await visit("/pos");
  await shot("pos-1-list");
  const po = (await get("/api/pos")).find((x) => x.po_number === "PO-1001");
  await visit(`/pos/${po.id}`);
  await shot("pos-2-detail");

  // ---- Agent Studio
  await visit("/studio?tab=agents&agent=policy");
  await p.locator(".cm-content").first().waitFor();
  await shot("studio-1-agents");
  // an invalid edit: inline marker + error list, Save disabled
  await p.locator(".cm-line", { hasText: "retries: 0" }).click();
  await p.keyboard.press("End");
  await p.keyboard.press("Backspace");
  await p.keyboard.type("lots");
  await p.getByText(/problem.*would not be saved/).waitFor({ timeout: 10_000 });
  await shot("studio-2-invalid-edit");
  await p.getByRole("button", { name: "Discard" }).click();
  await visit("/studio?tab=agents&agent=extraction");
  await p.getByRole("tab", { name: /extraction\.md/ }).click();
  await p.locator(".cm-content").first().waitFor();
  await shot("studio-3-prompt");
  await visit("/studio?tab=workflow");
  await p.locator(".react-flow__node").first().waitFor();
  await p.waitForTimeout(800);
  await shot("studio-4-workflow-graph");

  // done-check: price_tolerance.pct 2 -> 5 in the rules table, test, save, close the first S08, re-run it
  await visit("/studio?tab=rules");
  const row = p.locator("tr", { has: p.getByText("price_tolerance", { exact: true }) });
  const input = row.getByRole("textbox", { name: "pct" });
  await input.fill("5");
  await p.getByText(/the whole config is valid/).waitFor({ timeout: 10_000 });
  await row.scrollIntoViewIfNeeded();
  await shot("studio-5-rules-pct-5-unsaved");
  // dry run of policy on the first S08 with the unsaved edit (drafts survive switching Studio tabs)
  await p.getByRole("tab", { name: "Agents" }).click();
  await p.locator("[role=button]", { hasText: POLICY_NAME }).first().click();
  await p.getByRole("tab", { name: "Test agent" }).click();
  await p.getByRole("combobox", { name: "Case" }).selectOption(String(s08.id));
  await p.getByRole("button", { name: /^Test / }).click();
  await p.getByText(/outcome/).first().waitFor({ timeout: 30_000 });
  await shot("studio-6-test-agent-dry-run");
  await p.getByRole("tab", { name: "Rules" }).click();
  await p.getByRole("button", { name: "Save rules" }).click();
  await p.getByText(/Rules saved as v/).waitFor();
  console.log("pct after save:", await pct());

  // the first S08 is still open and would make the new run a duplicate: reject it, then re-run S08 from the launcher
  await fetch(`${api}/api/cases/${s08.id}/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "reject", text: "Closing to re-run with a 5% price tolerance", actor: "Admin" }),
  });
  await visit("/scenarios");
  await shot("launcher");
  const card = p.locator("div.rounded-lg.border", { has: p.getByText("S08_price_variance", { exact: true }) });
  await Promise.all([p.waitForURL(/\/cases\/\d+\/live/), card.getByRole("button", { name: "Run" }).click()]);
  const newId = Number(p.url().match(/cases\/(\d+)/)[1]);
  const rerun = await settle(newId);
  console.log("S08 re-run:", rerun.ref, rerun.decision, rerun.status);
  if (rerun.decision !== "auto_approve") throw new Error(`S08 re-run should auto-approve, got ${rerun.decision}`);
  await settled();
  await p.waitForTimeout(1500);
  await shot("s08-rerun-auto-approved");

  // set it back: reset rules.yaml to the file
  await visit("/studio?tab=rules");
  await p.getByRole("radio", { name: "Edit as YAML" }).click();
  await p.getByRole("button", { name: "Reset to file" }).click();
  await p.getByRole("dialog").getByRole("button", { name: "Reset to file" }).click();
  await p.getByText(/reset to the file/).waitFor();
  await shot("studio-7-reset-to-file");
  console.log("pct after reset:", await pct());
} else {
  for (const [name, path] of [
    ["library-1-selected", "/library?exported=all"],
    ["library-3-history", "/library?tab=history"],
    ["vendors-1-list", "/vendors"],
    ["pos-1-list", "/pos"],
    ["studio-1-agents", "/studio?tab=agents&agent=policy"],
    ["studio-4-workflow-graph", "/studio?tab=workflow"],
    ["studio-5-rules", "/studio?tab=rules"],
    ["launcher", "/scenarios"],
  ]) {
    await visit(path);
    if (name === "library-1-selected") await p.getByRole("checkbox", { name: "Select all" }).check();
    if (path.includes("workflow")) await p.locator(".react-flow__node").first().waitFor();
    await p.waitForTimeout(600);
    await shot(name);
  }
  const vendors = await get("/api/vendors");
  const vendor = vendors.find((v) => v.status === "pending_docs") ?? vendors.at(-1);
  await visit(`/vendors/${vendor.id}`);
  await shot("vendors-2-detail-checks");
  const po = (await get("/api/pos")).find((x) => x.po_number === "PO-1001");
  await visit(`/pos/${po.id}`);
  await shot("pos-2-detail");
  const withPortal = (await get("/api/cases?status=on_hold&page_size=20")).items[0];
  if (withPortal) {
    const j = await get(`/api/cases/${withPortal.id}/journey`);
    if (j.portal_url) {
      await visit(`/portal/${j.portal_url.split("/portal/")[1]}`);
      await shot("portal-1-request");
    }
  }
}
await browser.close();
