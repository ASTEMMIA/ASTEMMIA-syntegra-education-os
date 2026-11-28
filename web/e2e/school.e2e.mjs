// Walk through registration, classes, curriculum and teaching staff (ADR-029) as each role.
//   BASE=http://127.0.0.1:8077 FIXTURES=web/e2e/fixtures SHOTS=/tmp/shots node web/e2e/school.e2e.mjs
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://127.0.0.1:8077";
const FIXTURES = process.env.FIXTURES ?? "web/e2e/fixtures";
const SHOTS = process.env.SHOTS ?? "/tmp/school-shots";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const errors = [];
let step = 0;

async function as(login, fn, { width = 1360 } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${login}: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !/status of (401|403|404|409|422)/.test(m.text()) && errors.push(`${login}: ${m.text()}`));
  await page.goto(`${BASE}/app/`);
  await page.getByLabel("Organisation code").fill("NG-AB");
  await page.getByLabel("Login (development sign-in)").fill(login);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: "Sign out" }).waitFor();
  try {
    await fn(page);
  } catch (e) {
    await page.screenshot({ path: `${SHOTS}/FAILED-${login}.png`, fullPage: true }).catch(() => {});
    throw e;
  } finally {
    await ctx.close();
  }
}

async function shot(page, name) {
  step += 1;
  await page.screenshot({ path: `${SHOTS}/${String(step).padStart(2, "0")}-${name}.png`, fullPage: true });
}

// 1. The head teacher opens Primary 5A and gives Mathematics to Ada Okeke.
await as("head@central.example", async (p) => {
  await p.getByRole("link", { name: "Classes", exact: true }).click();
  await p.getByRole("link", { name: "Primary 4A" }).waitFor();
  await p.getByText("Open a class").click();
  await p.getByLabel("Year", { exact: true }).selectOption({ label: "Primary 5" });
  await p.getByLabel("Arm").fill("A");
  await p.getByLabel("Capacity").fill("35");
  await p.getByLabel("Class teacher").selectOption({ label: "Uche Nwosu" });
  await p.getByRole("button", { name: "Open the class" }).click();
  await p.getByRole("link", { name: "Primary 5A" }).click();
  await p.getByRole("heading", { name: "Subjects and teachers" }).waitFor();
  await p.getByLabel("Teacher for Mathematics").selectOption({ label: "Ada Okeke" });
  await p.getByText(/no current TRCN registration/).waitFor();
  await shot(p, "head-class-subjects");
});

// 2. The registrar records an application; the head offers a place; the registrar enrols.
await as("registrar@central.example", async (p) => {
  await p.getByRole("link", { name: "Admissions" }).click();
  await p.getByText("Record an application").click();
  await p.getByLabel(/Class wanted/).selectOption({ label: "Primary 5" });
  await p.getByLabel("First name").fill("Amara");
  await p.getByLabel("Surname").fill("Okafor");
  await p.getByLabel("Date of birth").fill("2016-06-02");
  await p.getByLabel("Parent or guardian").fill("Obinna Okafor");
  await p.getByLabel("Their phone").fill("0803 765 4321");
  await p.getByLabel("Relationship").selectOption("father");
  await p.getByRole("button", { name: "Record the application" }).click();
  await p.getByRole("button", { name: /ADM-\d{4}-\d{6}/ }).click();
  await p.getByRole("button", { name: "Start the review" }).click();
  await p.getByText("Under review").first().waitFor();
  assert.equal(await p.getByRole("button", { name: "Offer a place" }).count(), 0, "registrars do not decide");
});
await as("head@central.example", async (p) => {
  await p.getByRole("link", { name: "Admissions" }).click();
  await p.getByRole("button", { name: /ADM-/ }).click();
  await p.getByRole("button", { name: "Offer a place" }).click();
  await p.getByRole("button", { name: "Family accepted" }).waitFor();
  await shot(p, "head-offered");
});
await as("registrar@central.example", async (p) => {
  await p.getByRole("link", { name: "Admissions" }).click();
  await p.getByRole("button", { name: /ADM-/ }).click();
  await p.getByRole("button", { name: "Family accepted" }).click();
  await p.getByLabel("Class", { exact: true }).selectOption({ label: "Primary 5A (35 places)" });
  await p.getByLabel("Admission number").fill("2026/0201");
  await p.getByRole("button", { name: "Enrol Amara" }).click();
  await p.getByRole("status").filter({ hasText: /Enrolled Amara Okafor \(LRN-/ }).waitFor();
  await p.getByText("in Primary 5A").waitFor();
  await shot(p, "registrar-enrolled");
});

// 3. The registrar imports Primary 4A's existing class list.
await as("registrar@central.example", async (p) => {
  await p.getByRole("link", { name: "Learners" }).click();
  await p.getByRole("link", { name: "Import a class list" }).click();
  await p.getByLabel("Class list (CSV)").setInputFiles(`${FIXTURES}/primary-4a.csv`);
  await p.getByRole("button", { name: "Check it" }).click();
  await p.getByText("2 ready, 1 to correct").waitFor();
  await p.getByText(/class 'Primary 9Z' is not open/).waitFor();
  await p.getByRole("button", { name: "Enrol the 2 ready pupils" }).click();
  await p.getByText("2 pupils enrolled").waitFor();
  await shot(p, "registrar-import");
});

// 4. Ada, the class teacher, sees her classes and the class list.
await as("teacher@central.example", async (p) => {
  await p.getByRole("link", { name: "My classes" }).click();
  await p.getByRole("link", { name: /Primary 5A/ }).waitFor();
  await p.getByRole("link", { name: /Primary 4A/ }).click();
  await p.getByRole("link", { name: /Okoro, Adaeze/ }).waitFor();
  assert.equal(await p.getByRole("link", { name: "Admissions" }).count(), 0, "teachers do not run admissions");
  await shot(p, "teacher-class");
});

// 5. Amara's family moves: Aba asks Township to take her; Township accepts.
await as("registrar@central.example", async (p) => {
  await p.getByRole("link", { name: "Learners" }).click();
  await p.getByLabel("Find a learner").fill("Okafor");
  await p.getByRole("button", { name: "Find" }).click();
  await p.getByRole("link", { name: /Amara/ }).click();
  await p.getByLabel("Receiving school").fill("Township");
  await p.getByRole("option", { name: /Township/ }).click();
  await p.getByLabel("Reason").first().fill("The family is moving to Umuahia");
  await p.getByRole("button", { name: "Ask them to take the learner" }).click();
  await p.getByRole("button", { name: "Ask them to take the learner" }).waitFor();
});
await as("registrar@township.example", async (p) => {
  await p.getByRole("link", { name: "Admissions" }).click();
  await p.getByRole("tab", { name: "Transfers" }).click();
  await p.getByRole("heading", { name: "Amara Okafor" }).waitFor();
  await p.getByRole("button", { name: "Accept" }).click();
  await p.getByText("Accepted").first().waitFor();
  await shot(p, "township-accepted");
});

// 6. Phone width: a class page without sideways scrolling.
await as("head@central.example", async (p) => {
  await p.getByRole("link", { name: "Classes", exact: true }).click();
  await p.getByRole("link", { name: "Primary 4A" }).click();
  await p.getByRole("heading", { name: "Class list" }).waitFor();
  const wide = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  assert.equal(wide, false, "the class page scrolls sideways on a phone");
  await shot(p, "phone-class");
}, { width: 390 });

await browser.close();
if (errors.length) {
  console.error("Browser errors:\n" + errors.join("\n"));
  process.exit(1);
}
console.log(`ok: ${step} screens checked, screenshots in ${SHOTS}`);
