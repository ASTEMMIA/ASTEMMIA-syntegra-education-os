// Attendance and examinations (ADR-032): the head takes a register, Ada enters Primary 4A's
// Mathematics tests and submits them, the head approves them and sets an examination paper.
// Runs after school.e2e.mjs (Primary 4A's imported pupils).
//   BASE=http://127.0.0.1:8077 SHOTS=/tmp/shots node web/e2e/exams.e2e.mjs
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://127.0.0.1:8077";
const SHOTS = process.env.SHOTS ?? "/tmp/exams-shots";
mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
const errors = [];
let step = 0;

// The seeded 2026/2027 terms; the walk-through uses the latest school day up to today.
const TERMS = [["2026-09-14", "2026-12-18"], ["2027-01-11", "2027-04-09"], ["2027-04-26", "2027-07-23"]];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function schoolDay() {
  const d = new Date();
  for (let i = 0; i < 400; i++, d.setDate(d.getDate() - 1)) {
    const s = iso(d);
    if (d.getDay() > 0 && d.getDay() < 6 && TERMS.some(([a, b]) => a <= s && s <= b)) return s;
  }
  return TERMS[0][0];
}
const DAY = schoolDay();

async function as(login, fn) {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${login}: ${e.message}`));
  p.on("console", (m) => m.type() === "error" && !/status of (401|403|404|409|422)/.test(m.text()) && errors.push(`${login}: ${m.text()}`));
  await p.goto(`${BASE}/app/`);
  await p.getByLabel("Organisation code").fill("NG-AB");
  await p.getByLabel("Login (development sign-in)").fill(login);
  await p.getByRole("button", { name: "Sign in", exact: true }).click();
  await p.getByRole("button", { name: "Sign out" }).waitFor();
  try {
    await fn(p);
  } catch (e) {
    await p.screenshot({ path: `${SHOTS}/FAILED-${login}.png`, fullPage: true }).catch(() => {});
    throw e;
  } finally {
    await ctx.close();
  }
}
async function shot(p, name) {
  step += 1;
  await p.screenshot({ path: `${SHOTS}/${String(step).padStart(2, "0")}-${name}.png`, fullPage: true });
}

// 1. The head takes Primary 4A's morning register (any day: the head may correct past days).
await as("head@central.example", async (p) => {
  await p.getByRole("link", { name: "Attendance", exact: true }).click();
  await p.getByLabel("Class", { exact: true }).selectOption({ label: "Primary 4A" });
  await p.getByLabel("Date").fill(DAY);
  await p.getByLabel("Register").selectOption("am");
  await p.getByRole("button", { name: "Mark everyone present" }).click();
  await p.getByRole("radiogroup", { name: /Ibe, Uchenna/ }).getByRole("radio", { name: "Late" }).click();
  await p.getByText(/· 1 late · 0 absent · 0 excused, of \d+/).waitFor();
  await p.getByRole("button", { name: /Save register|Save changes/ }).click();
  await p.getByText("Register saved.").waitFor();
  await p.getByText(/Primary 4A · Morning/).waitFor();
  await p.getByRole("cell", { name: "Primary 4A" }).first().waitFor();
  await shot(p, "head-register");
});

// 2. Ada enters Primary 4A's Mathematics tests and examination, and submits them.
await as("teacher@central.example", async (p) => {
  await p.getByRole("link", { name: "Markbook" }).click();
  await p.getByLabel("Subject").selectOption({ label: "Mathematics · Primary 4A" });
  await p.getByRole("heading", { name: /Mathematics · Primary 4A/ }).waitFor();
  const parts = { "First test": [17, 12], "Second test": [15.5, 14], Examination: [48, 39] };
  let changes = 0;
  for (const [part, values] of Object.entries(parts)) {
    const boxes = p.getByLabel(new RegExp(`: ${part}$`));
    const n = await boxes.count();
    for (let i = 0; i < n; i++) await boxes.nth(i).fill(String(values[i % 2]));
    changes += n;
  }
  await p.getByRole("button", { name: `Save ${changes} changes` }).click();
  await p.getByText(new RegExp(`^${changes / 3} of ${changes / 3} complete`)).waitFor();
  await p.getByRole("cell", { name: "80.50" }).first().waitFor();
  await shot(p, "teacher-markbook");
  await p.getByRole("button", { name: "Submit for approval" }).click();
  await p.getByText("Waiting for approval").waitFor();
  assert.equal(await p.getByLabel(/: First test$/).count(), 0, "locked once submitted");
});

// 3. The head approves the Mathematics results and sets the examination paper.
await as("head@central.example", async (p) => {
  await p.getByRole("link", { name: "Results" }).click();
  const row = p.getByRole("row", { name: /Primary 4A.*Mathematics/ });
  await row.getByRole("button", { name: "Open" }).click();
  await p.getByRole("button", { name: "Approve" }).click();
  await p.locator(".panel").getByText("Approved", { exact: true }).waitFor();
  await shot(p, "head-results");
  // Every Primary 4A subject is now approved: publish its report cards and open one.
  const p4a = p.locator("li", { hasText: "Primary 4A" });
  await p4a.getByRole("button", { name: /Publish \d+ report cards/ }).click();
  await p4a.getByText(/\d+ published/).waitFor();
  await p.getByRole("link", { name: "Learners" }).click();
  await p.getByLabel("Find a learner").fill("Ibe");
  await p.getByRole("button", { name: "Find" }).click();
  await p.getByRole("link", { name: /Uchenna/ }).click();
  await p.getByRole("link", { name: /First term, 2026\/2027/ }).click();
  await p.getByText("Report card", { exact: true }).waitFor();
  await p.getByRole("cell", { name: "Mathematics" }).waitFor();
  await shot(p, "report-card");
  await p.getByRole("link", { name: "Results" }).click();
  await p.getByRole("link", { name: "Exams" }).click();
  await p.getByLabel("Subject").selectOption({ label: "Primary 4A · English Studies" });
  await p.getByLabel("Date").fill(DAY);
  await p.getByLabel("Starts").fill("09:00");
  await p.getByLabel("Minutes").fill("90");
  await p.getByRole("button", { name: "Add to the timetable" }).click();
  await p.getByText(/09:00–10:30/).waitFor();
  await shot(p, "head-exam-timetable");
});

await browser.close();
assert.deepEqual(errors, [], "no page errors");
console.log(`exams walk-through passed (school day ${DAY}); screenshots in ${SHOTS}`);
