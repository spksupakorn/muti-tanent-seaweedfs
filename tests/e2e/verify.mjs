// Real-browser check of the React app against the running compose stack (run it with scripts/e2e.sh).
// Covers the acceptance criteria of the file browser: login, tenant switching, folders, upload progress,
// the 100 MB message, download bytes, token refresh and sign-out. Extend it as screens are added.
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { EMAIL, PW, TENANT_A, TENANT_B, TID_A, TOKEN_A } = process.env;
const APP = "http://127.0.0.1:5173";
const API = "http://127.0.0.1:8000";
const sha = (b) => createHash("sha256").update(b).digest("hex");
const results = [];
const check = (name, ok, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  — " + extra : ""}`);
};

// A file in a nested folder, put there through the API so the UI has something to navigate into.
const nested = Buffer.from("nested file body");
const form = new FormData();
form.append("file", new Blob([nested]), "inner.txt");
form.append("folder", "/docs/inner");
const r = await fetch(`${API}/api/files`, { method: "POST", headers: { Authorization: `Bearer ${TOKEN_A}`, "X-Tenant-ID": TID_A }, body: form });
check("seed: nested file uploaded through the API", r.status === 200);

const browser = await chromium.launch();
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();
const posts = [];
page.on("request", (req) => req.method() === "POST" && req.url().endsWith("/api/files") && posts.push(req.url()));
const consoleErrors = [];
page.on("pageerror", (e) => consoleErrors.push(String(e)));

// --- login
await page.goto(APP);
await page.getByLabel("Email").fill(EMAIL);
await page.getByLabel("Password").fill("wrong-password");
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByRole("alert").waitFor();
check("wrong password shows a clear message", (await page.getByRole("alert").innerText()) === "Wrong email or password");

await page.getByLabel("Password").fill(PW);
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByLabel("Organization").waitFor();
const options = await page.getByLabel("Organization").locator("option").allInnerTexts();
check("after login both organizations are listed with the user's role", options.length === 2 && options.some((o) => o.includes(TENANT_A) && o.includes("admin")) && options.some((o) => o.includes(TENANT_B) && o.includes("member")), options.join(" | "));

// --- tenant A: empty root except the folder "docs"
await page.getByLabel("Organization").selectOption({ label: `${TENANT_A} (admin)` });
await page.getByText("📁 docs").waitFor();
check("tenant A root shows the docs folder", true);

// --- upload with progress (large enough that the bar is observable)
const big = Buffer.alloc(60 * 1024 * 1024, 7);
const work = mkdtempSync(join(tmpdir(), "filestore-e2e-"));
writeFileSync(join(work, "big.bin"), big);
const seen = [];
const sampler = (async () => {
  const bar = page.getByRole("progressbar");
  const end = Date.now() + 60000;
  while (Date.now() < end) {
    if (await bar.count()) seen.push(Number(await bar.getAttribute("aria-valuenow")));
    else if (seen.length) return;
    await page.waitForTimeout(40);
  }
})();
await page.getByLabel("Choose a file to upload").setInputFiles(join(work, "big.bin"));
await page.getByRole("status").waitFor({ timeout: 60000 });
await sampler;
const distinct = [...new Set(seen)];
check("upload shows a progress bar that advances", distinct.length >= 3 && Math.max(...seen) >= 99, `samples: ${distinct.slice(0, 8).join(",")}…`);
check("uploaded file is listed", (await page.getByRole("cell", { name: "big.bin" }).count()) === 1);

// --- download returns identical bytes
const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("row", { name: /big\.bin/ }).getByRole("button", { name: "Download" }).click()]);
const path = await dl.path();
check("download is byte-identical to the original", sha(readFileSync(path)) === sha(big) && dl.suggestedFilename() === "big.bin");

// --- folder navigation: in and out
await page.getByText("📁 docs").click();
await page.getByText("📁 inner").waitFor();
await page.getByText("📁 inner").click();
await page.getByRole("cell", { name: "inner.txt" }).waitFor();
check("navigating into docs/inner shows the nested file", true);
const [dl2] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download" }).click()]);
check("a nested file downloads too", readFileSync(await dl2.path()).equals(nested));
await page.getByRole("button", { name: "↑ Up" }).click();
await page.getByText("📁 inner").waitFor();
check("Up goes back one level", (await page.getByRole("cell", { name: "inner.txt" }).count()) === 0);
await page.getByRole("navigation").getByRole("button", { name: "All files" }).click();
await page.getByText("📁 docs").waitFor();
check("breadcrumb returns to the root", (await page.getByRole("cell", { name: "big.bin" }).count()) === 1);

// --- over the limit: refused in the browser, nothing is sent
const before = posts.length;
writeFileSync(join(work, "huge.bin"), Buffer.alloc(100 * 1024 * 1024 + 1));
await page.getByLabel("Choose a file to upload").setInputFiles(join(work, "huge.bin"));
await page.getByRole("alert").waitFor();
check("a file over 100 MB gets a clear message", (await page.getByRole("alert").innerText()).includes("100 MB"));
check("and is not sent to the server", posts.length === before);

// --- tenant isolation in the UI
await page.getByLabel("Organization").selectOption({ label: `${TENANT_B} (member)` });
await page.getByText("This folder is empty.").waitFor();
check("tenant B does not show tenant A's files", (await page.getByRole("cell", { name: "big.bin" }).count()) === 0);
await page.getByLabel("Choose a file to upload").setInputFiles({ name: "b-only.txt", mimeType: "text/plain", buffer: Buffer.from("B") });
await page.getByRole("cell", { name: "b-only.txt" }).waitFor();
await page.getByLabel("Organization").selectOption({ label: `${TENANT_A} (admin)` });
await page.getByRole("cell", { name: "big.bin" }).waitFor();
check("switching back shows only tenant A's files", (await page.getByRole("cell", { name: "b-only.txt" }).count()) === 0);

// --- expired access token: refreshed silently
await page.evaluate(() => {
  const t = JSON.parse(localStorage.getItem("filestore.tokens"));
  localStorage.setItem("filestore.tokens", JSON.stringify({ ...t, access: "expired.invalid.token" }));
});
await page.getByText("📁 docs").click(); // any request now starts with a bad access token
await page.getByText("📁 inner").waitFor();
const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("filestore.tokens")));
check("an expired access token is refreshed without a new login", stored.access !== "expired.invalid.token" && (await page.getByLabel("Organization").count()) === 1);

// --- dead session: back to login
await page.evaluate(() => localStorage.setItem("filestore.tokens", JSON.stringify({ access: "x.y.z", refresh: "x.y.z" })));
await page.getByRole("navigation").getByRole("button", { name: "All files" }).click();
await page.getByRole("button", { name: "Sign in" }).waitFor();
check("when the session cannot be refreshed the user lands on the login page", true);
check("and the stored tokens are gone", (await page.evaluate(() => localStorage.getItem("filestore.tokens"))) === null);

// --- sign out
await page.getByLabel("Email").fill(EMAIL);
await page.getByLabel("Password").fill(PW);
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByLabel("Organization").waitFor();
await page.screenshot({ path: join(work, "final.png") });
await page.getByRole("button", { name: "Sign out" }).click();
await page.getByRole("button", { name: "Sign in" }).waitFor();
check("sign out returns to the login page", true);
check("no uncaught errors in the page", consoleErrors.length === 0, consoleErrors.join(";"));

await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
rmSync(work, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
