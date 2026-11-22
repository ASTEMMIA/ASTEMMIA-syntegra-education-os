// End-to-end walk through the records app against a running API with the seeded test school.
//   BASE=http://127.0.0.1:8077 SCANS=/path/to/jpgs SHOTS=/tmp/shots node web/e2e/records.e2e.mjs
// Uses the Playwright that is installed on the machine (require('playwright')).
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://127.0.0.1:8077";
const SCANS = process.env.SCANS;
const SHOTS = process.env.SHOTS ?? "/tmp/records-shots";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const errors = [];
let step = 0;

async function as(login, fn, { width = 1360 } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${login}: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !/status of (401|403|409|422)/.test(m.text()) && errors.push(`${login}: ${m.text()}`));
  await page.goto(`${BASE}/app/`);
  await page.getByLabel("Organisation code").fill("NG-AB");
  await page.getByLabel("Login (development sign-in)").fill(login);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: "Sign out" }).waitFor();
  try {
    await fn(page);
  } finally {
    await ctx.close();
  }
}

async function shot(page, name) {
  step += 1;
  await page.screenshot({ path: `${SHOTS}/${String(step).padStart(2, "0")}-${name}.png`, fullPage: true });
}

// 1. The records officer opens a batch for the contractor.
await as("records@central.example", async (p) => {
  await p.getByRole("link", { name: "Batches" }).click();
  await p.getByText("Open a batch").click();
  await p.getByLabel("What is in the box").fill("Pupils' files E, and 2025 minutes (box 3)");
  await p.getByLabel("A contractor").check();
  await p.getByLabel("Contractor", { exact: true }).fill("Imaging Services Ltd");
  await p.getByLabel("Contract reference").fill("ASUBEB/DIG/2026/01");
  await p.getByLabel("Operator").fill("scanner");
  await p.getByRole("option", { name: /scanner@contractor/ }).click();
  await p.getByRole("button", { name: "Open the batch" }).click();
  await p.getByRole("link", { name: /BAT-\d{4}-\d{6}/ }).waitFor();
  await shot(p, "officer-batches");
});

// 2. The contractor's operator scans into it, then finishes.
await as("scanner@contractor.example", async (p) => {
  assert.equal(await p.getByRole("link", { name: "Search" }).count(), 1);
  assert.equal(await p.getByRole("link", { name: "Files" }).count(), 0, "operators do not see the file register");
  await p.getByRole("link", { name: "My scanning" }).click();
  await p.locator('input[type="file"]').first().setInputFiles([`${SCANS}/scan-admission.jpg`, `${SCANS}/scan-report.jpg`, `${SCANS}/scan-minutes.jpg`]);
  await p.getByLabel("Title for scan-minutes.jpg").fill("PTA minutes, 6 March 2025");
  await p.getByLabel("Type for scan-minutes.jpg").selectOption("minutes");
  await shot(p, "operator-queue");
  await p.getByRole("button", { name: "Send 3 scans" }).click();
  await p.getByRole("button", { name: "All sent" }).waitFor();
  await p.getByRole("button", { name: /Capture finished/ }).click();
  await p.getByText("Waiting for its check").or(p.getByText("Captured", { exact: true })).first().waitFor();
  await shot(p, "operator-finished");
  // An operator cannot read what they scanned through search.
  await p.getByRole("link", { name: "Search" }).click();
  await p.getByText("No records yet").waitFor();
});

// 3. The records officer checks the batch and confirms each document's details.
await as("records@central.example", async (p) => {
  await p.getByRole("link", { name: "Batches" }).click();
  await p.getByRole("link", { name: /BAT-/ }).first().click();
  await p.getByLabel("Documents sampled").fill("1");
  await p.getByRole("button", { name: "Accept the batch" }).click();
  await p.getByText("Accepted").first().waitFor();
  await p.getByRole("link", { name: "Scan 0001" }).click();
  await p.locator(".stage img").waitFor();
  await p.getByLabel("Title", { exact: true }).fill("Admission form, Chidi Emmanuel Eze");
  await p.getByLabel("Type", { exact: true }).selectOption("admission_form");
  await p.getByLabel("Date on the document").fill("2022-09-12");
  await p.getByLabel("Keywords").fill("admission, primary 1");
  await p.getByLabel("Paper file").selectOption({ label: "LRN/2/0001 · Eze, Chidi Emmanuel" });
  await p.getByLabel("Folio (position in the file)").fill("1");
  await p.getByRole("button", { name: "Save the details" }).click();
  await p.getByText("Indexed").first().waitFor();
  await shot(p, "officer-indexed");
  // The same person cannot verify their own indexing.
  await p.getByRole("button", { name: "Verify" }).click();
  await p.getByText(/cannot verify it/).waitFor();
  await p.goBack();
  await p.getByRole("link", { name: "PTA minutes, 6 March 2025" }).click();
  await p.getByLabel("Paper file").selectOption({ label: "GOV/2/2025/1 · PTA and SBMC minutes 2025" });
  await p.getByLabel("Folio (position in the file)").fill("7");
  await p.getByLabel("Date on the document").fill("2025-03-06");
  await p.getByRole("button", { name: "Save the details" }).click();
  await p.getByText("Indexed").first().waitFor();
});

// 4. A second records officer verifies them.
await as("records2@central.example", async (p) => {
  await p.getByLabel("Status").selectOption("indexed");
  for (const title of ["Admission form, Chidi Emmanuel Eze", "PTA minutes, 6 March 2025"]) {
    await p.getByRole("link", { name: title }).click();
    await p.getByRole("button", { name: "Verify" }).click();
    await p.getByText(/Verified by/).waitFor();
    await p.goBack();
  }
  await p.getByLabel("Status").selectOption("verified");
  await p.getByRole("link", { name: "Admission form, Chidi Emmanuel Eze" }).waitFor();
  await shot(p, "verified-search");
});

// 5. A class teacher reads Internal records only: the confidential scans do not appear.
await as("teacher@central.example", async (p) => {
  await p.getByLabel("Search words").fill("eze");
  await p.getByRole("button", { name: "Search" }).click();
  await p.getByText(/Nothing found/).waitFor();
  // Filed in an Internal file, the minutes became Internal: the teacher finds them.
  await p.getByLabel("Search words").fill("PTA minutes");
  await p.getByRole("button", { name: "Search" }).click();
  await p.getByRole("link", { name: "PTA minutes, 6 March 2025" }).waitFor();
  await shot(p, "teacher-search");
});

// 6. The head teacher finds the admission form, reads it, and hands the paper file to the teacher.
await as("head@central.example", async (p) => {
  await p.getByLabel("Search words").fill("admission eze");
  await p.getByRole("button", { name: "Search" }).click();
  await p.getByRole("link", { name: "Admission form, Chidi Emmanuel Eze" }).click();
  await p.locator(".stage img").waitFor();
  await shot(p, "head-document");
  await p.getByRole("link", { name: /LRN\/2\/0001/ }).click();
  await p.getByLabel("Person", { exact: true }).fill("teacher");
  await p.getByRole("option", { name: /teacher@central/ }).click();
  await p.getByLabel("Note", { exact: true }).fill("For the transfer letter");
  await p.getByRole("button", { name: "Record the movement" }).click();
  await p.getByText(/Out with/).first().waitFor();
  assert.equal(await p.getByLabel("Pass to another person").isChecked(), true, "the form resets after a movement");
  assert.equal(await p.getByLabel("Person", { exact: true }).inputValue(), "");
  await shot(p, "head-file-out");
});

// 7. The State Archives oversee everything from the state root, and see who opened what.
await as("archives@abia.example", async (p) => {
  await p.getByRole("link", { name: "Overview" }).click();
  await p.getByRole("cell", { name: "TEST Central School Aba" }).waitFor();
  await shot(p, "archives-overview");
  await p.getByRole("link", { name: "Search" }).click();
  await p.getByRole("link", { name: "Admission form, Chidi Emmanuel Eze" }).click();
  await p.getByRole("heading", { name: "Who opened it" }).waitFor();
  await p.getByRole("cell", { name: "head@central.example" }).first().waitFor();
  await shot(p, "archives-access-log");
});

// 8. Phone width: the search and a document still work without sideways scrolling.
await as("head@central.example", async (p) => {
  await p.getByRole("link", { name: "PTA minutes, 6 March 2025" }).click();
  await p.locator(".stage img").waitFor();
  const wide = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  assert.equal(wide, false, "the page scrolls sideways on a phone");
  await shot(p, "phone-document");
}, { width: 390 });

await browser.close();
if (errors.length) {
  console.error("Browser errors:\n" + errors.join("\n"));
  process.exit(1);
}
console.log(`ok: ${step} screens checked, screenshots in ${SHOTS}`);
