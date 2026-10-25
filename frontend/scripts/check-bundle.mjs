// Fails when a production build contains the AUTH_MODE=dev stub login (build brief: "AUTH_MODE=dev").
// Runs after `vite build` (npm run build) and on its own as `npm run test:bundle`.
// A build made with VITE_DEV_LOGIN=true (e2e against a dev-mode API) includes it on purpose: skipped.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist", import.meta.url));
// strings that exist only in src/lib/devLogin.tsx
const MARKERS = ["/api/auth/dev-login", "dev-login", "lf-dev-session", "Dev sign-in", "AUTH_MODE=dev"];

if (process.env.VITE_DEV_LOGIN === "true") {
  console.log("check-bundle: skipped (VITE_DEV_LOGIN=true build includes the dev login on purpose)");
  process.exit(0);
}

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

let all;
try {
  all = files(DIST).filter((f) => /\.(js|mjs|html|css|map|json|txt)$/.test(f));
} catch {
  console.error(`check-bundle: ${DIST} not found; run vite build first`);
  process.exit(1);
}
const hits = [];
for (const f of all) {
  const text = readFileSync(f, "utf8");
  for (const m of MARKERS) if (text.includes(m)) hits.push(`${f}: contains "${m}"`);
}
if (hits.length) {
  console.error("check-bundle: the production bundle contains the dev login:\n  " + hits.join("\n  "));
  process.exit(1);
}
console.log(`check-bundle: OK, no dev login in ${all.length} built files`);
