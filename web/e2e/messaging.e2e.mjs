// Walk through secure messaging (ADR-030): a registrar gives a mother a parent account; the class
// teacher writes to the class's parents with a letter attached; the mother signs in with an
// emailed code, reads, downloads, replies and writes to the teacher; the head handles a report.
// Runs after school.e2e.mjs (it uses the Primary 4A pupils that walk-through imports).
//   BASE=http://127.0.0.1:8077 DB=/tmp/e2e.db FIXTURES=web/e2e/fixtures SHOTS=/tmp/shots node web/e2e/messaging.e2e.mjs
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://127.0.0.1:8077";
const DB = process.env.DB;
const FIXTURES = process.env.FIXTURES ?? "web/e2e/fixtures";
const SHOTS = process.env.SHOTS ?? "/tmp/messaging-shots";
const MOTHER = "ifeoma.okoro@example.com";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const errors = [];
let step = 0;

async function shot(page, name) {
  step += 1;
  await page.screenshot({ path: `${SHOTS}/${String(step).padStart(2, "0")}-${name}.png`, fullPage: false });
}

async function open(name, fn, { width = 1360, signIn }) {
  const ctx = await browser.newContext({ viewport: { width, height: 860 }, acceptDownloads: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${name}: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !/status of (401|403|404|409|422)/.test(m.text()) && errors.push(`${name}: ${m.text()}`));
  await page.goto(`${BASE}/app/`);
  await page.getByLabel("Organisation code").fill("NG-AB");
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).waitFor();
  try {
    await fn(page);
  } catch (e) {
    await page.screenshot({ path: `${SHOTS}/FAILED-${name}.png`, fullPage: true }).catch(() => {});
    throw e;
  } finally {
    await ctx.close();
  }
}

const staff = (login) => async (p) => {
  await p.getByLabel("Login (development sign-in)").fill(login);
  await p.getByRole("button", { name: "Sign in", exact: true }).click();
};

/** The parent's inbox: the newest sign-in code queued for her (the dispatcher is not running). */
function latestCode() {
  const out = execFileSync("python3", [
    "-I",
    "-c",
    "import sqlite3,sys,re; c=sqlite3.connect(sys.argv[1]); " +
      "rows=c.execute(\"select body from outbound_email where purpose='sign-in-code' and to_address=? order by created_at desc limit 1\", (sys.argv[2],)).fetchall(); " +
      "print(re.search(r'\\b(\\d{6})\\b', rows[0][0]).group(1) if rows else '')",
    DB,
    MOTHER,
  ]).toString().trim();
  assert.match(out, /^\d{6}$/, "a sign-in code was queued");
  return out;
}

const mother = async (p) => {
  await p.getByRole("button", { name: "I am a parent or guardian" }).click();
  await p.getByLabel("Your email address").fill(MOTHER);
  await p.getByRole("button", { name: "Email me a code" }).click();
  await p.getByText(/a 6-digit code is on its way/).waitFor();
  await p.getByLabel("6-digit code").fill(latestCode());
  await p.getByRole("button", { name: "Sign in with the code" }).click();
  await p.getByRole("heading", { name: "My children" }).waitFor(); // a parent's home (ADR-032)
  await p.getByRole("link", { name: /^Messages/ }).click();
};

// 1. The registrar gives Adaeze's mother a parent account from Learner 360.
await open("registrar", async (p) => {
  await p.getByRole("link", { name: "Learners" }).click();
  await p.getByLabel("Find a learner").fill("Okoro");
  await p.getByRole("button", { name: "Find" }).click();
  await p.getByRole("link", { name: /Adaeze/ }).click();
  await p.getByRole("button", { name: "Give Ifeoma Okoro a parent account" }).click();
  await p.getByLabel("Ifeoma Okoro's email address").fill(MOTHER);
  await p.getByRole("button", { name: "Give account" }).click();
  await p.getByText("Has a parent account (messaging)").waitFor();
  await shot(p, "registrar-parent-account");
}, { signIn: staff("registrar@central.example") });

// 2. Ada, the class teacher, writes to Primary 4A's parents with the mid-term letter.
await open("teacher", async (p) => {
  await p.getByRole("link", { name: "My classes" }).click();
  await p.getByRole("link", { name: /Primary 4A/ }).click();
  await p.getByRole("button", { name: "Message the parents" }).click();
  await p.getByText("Primary 4A parents").first().waitFor();
  await p.getByRole("button", { name: "Attach documents" }).waitFor();
  await p.locator('input[type="file"]').setInputFiles(`${FIXTURES}/mid-term-letter.pdf`);
  await p.getByLabel("Message", { exact: true }).fill("Good afternoon. Mid-term break is Friday 4 to Monday 7 December; the letter is attached.");
  await p.getByRole("button", { name: "Send" }).click();
  await p.getByRole("button", { name: /mid-term-letter\.pdf/ }).waitFor();
  await p.getByText("Sent", { exact: false }).first().waitFor().catch(() => {});
  await shot(p, "teacher-class-conversation");
}, { signIn: staff("teacher@central.example") });

// 3. The mother signs in with an emailed code, reads and downloads the letter, and replies.
await open("mother", async (p) => {
  await p.getByRole("heading", { name: "Messages" }).waitFor();
  assert.equal(await p.getByRole("link", { name: "Records" }).count(), 0, "parents see messaging, not the records");
  const conv = p.getByRole("link", { name: /Primary 4A parents/ });
  await conv.getByLabel("1 unread").waitFor();
  await shot(p, "mother-inbox");
  await conv.click();
  const dl = p.waitForEvent("download");
  await p.getByRole("button", { name: /mid-term-letter\.pdf/ }).click();
  const file = await dl;
  assert.equal(file.suggestedFilename(), "mid-term-letter.pdf");
  assert.deepEqual(readFileSync(await file.path()), readFileSync(`${FIXTURES}/mid-term-letter.pdf`), "the document arrives intact");
  await p.getByLabel("Message", { exact: true }).fill("Thank you, received.");
  await p.keyboard.press("Enter");
  await p.locator(".msgs").getByText("Thank you, received.").waitFor();
  // A word with the teacher alone.
  await p.getByRole("button", { name: "New", exact: true }).click();
  await p.getByLabel("Who do you want to message?").fill("teacher");
  await p.getByRole("button", { name: /teacher@central\.example/ }).click();
  await p.locator(".thread-title").getByText("teacher@central.example").waitFor();
  await p.getByLabel("Message", { exact: true }).fill("Adaeze will be late on Monday; she has a dental appointment.");
  await p.getByRole("button", { name: "Send" }).click();
  await p.locator(".msgs").getByText("dental appointment").waitFor();
  await shot(p, "mother-direct");
  // Parents cannot reach other parents, or create groups.
  await p.getByRole("button", { name: "New", exact: true }).click();
  await p.getByLabel("Who do you want to message?").fill("Chuks");
  await p.getByText("Nobody you may message matches.").waitFor();
  assert.equal(await p.getByRole("button", { name: "Group", exact: true }).count(), 0);
}, { signIn: mother });

// 4. On a phone: one pane at a time.
await open("mother-phone", async (p) => {
  await p.getByRole("link", { name: /teacher@central\.example/ }).click();
  await p.locator(".msgs").getByText("dental appointment").waitFor();
  await p.getByRole("link", { name: "Back to conversations" }).waitFor();
  await shot(p, "mother-phone");
}, { width: 390, signIn: mother });

// 5. Ada reads it (the mother's ticks turn to read), reacts and answers; then reports a message.
await open("teacher2", async (p) => {
  const nav = p.getByRole("link", { name: /^Messages/ });
  await nav.getByLabel(/unread/).waitFor();
  await nav.click();
  await p.getByRole("link", { name: /Ifeoma Okoro/ }).click();
  await p.getByText("dental appointment").last().waitFor();
  await p.getByRole("button", { name: "Message options" }).last().click();
  await p.getByRole("button", { name: "React 👍" }).click();
  await p.getByRole("button", { name: "👍" }).first().waitFor();
  await p.getByLabel("Message", { exact: true }).fill("Noted, thank you. I will tell the first-period teacher.");
  await p.keyboard.press("Enter");
  await p.locator(".msgs").getByText("first-period teacher").waitFor();
  await shot(p, "teacher-direct");
  await p.getByRole("link", { name: /Primary 4A parents/ }).click();
  await p.locator(".msgs").getByText("Thank you, received.").waitFor();
  await p.getByRole("button", { name: "Message options" }).last().click();
  await p.getByRole("button", { name: "Report" }).click();
  await p.getByLabel("What is wrong with it?").fill("Checking the reporting route (walk-through)");
  await p.getByRole("button", { name: "Report" }).last().click();
  await p.getByText(/Reported\./).waitFor();
}, { signIn: staff("teacher@central.example") });

// 6. The head sees the report, reviews the conversation for a logged reason, and dismisses it.
await open("head", async (p) => {
  await p.getByRole("link", { name: "Reports" }).click();
  await p.getByText("Checking the reporting route").waitFor();
  await p.getByRole("button", { name: "Review the conversation" }).click();
  await p.getByLabel("Why do you need to read it?").fill("Walk-through: checking a reported message");
  await p.getByRole("button", { name: "Read the conversation" }).click();
  await p.getByText("your reading is logged").waitFor();
  await shot(p, "head-review");
  await p.getByRole("button", { name: "Dismiss" }).click();
  await p.getByText("Nothing to look at").waitFor();
}, { signIn: staff("head@central.example") });

// 7. The mother's message to Ada now shows as read.
await open("mother-again", async (p) => {
  await p.getByRole("link", { name: /teacher@central\.example/ }).click();
  await p.locator(".msgs").getByText("first-period teacher").waitFor();
  await p.getByLabel("Read", { exact: true }).first().waitFor();
}, { signIn: mother });

await browser.close();
assert.deepEqual(errors, [], "no page errors");
console.log(`messaging walk-through passed; screenshots in ${SHOTS}`);
