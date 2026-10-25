/** Unit tests of the open-redirect guard on /login?next= and /auth/callback (no browser needed). */
import { expect, test } from "@playwright/test";
import { loginPath, safeNext } from "../src/lib/safeNext";

const ORIGIN = "https://ledgerflow.example";

test("same-origin relative paths are followed, query and hash kept", () => {
  expect(safeNext("/cases/INV-2026-0003", ORIGIN)).toBe("/cases/INV-2026-0003");
  expect(safeNext("/studio?tab=rules#x", ORIGIN)).toBe("/studio?tab=rules#x");
  expect(safeNext("/cases/../dashboard", ORIGIN)).toBe("/dashboard"); // normalised, still ours
});

test("anything that could leave the origin is refused", () => {
  for (const bad of [
    "https://evil.example/",
    "//evil.example/path",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "evil.example",
    " /dashboard",
    "/\t/evil.example",
    "/\n/evil.example",
    "",
    null,
    undefined,
    "/" + "a".repeat(3000),
  ]) {
    expect(safeNext(bad, ORIGIN), String(bad)).toBeNull();
  }
});

test("no loops back into sign-in", () => {
  expect(safeNext("/login?next=/x", ORIGIN)).toBeNull();
  expect(safeNext("/auth/callback", ORIGIN)).toBeNull();
});

test("loginPath encodes the current location", () => {
  expect(loginPath("/cases/INV-1?a=1#b")).toBe("/login?next=%2Fcases%2FINV-1%3Fa%3D1%23b");
  expect(loginPath("/")).toBe("/login");
});
