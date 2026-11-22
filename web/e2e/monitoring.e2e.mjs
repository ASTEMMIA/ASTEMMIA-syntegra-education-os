// The Government Monitoring Dashboard (ADR-031) as the State officer, on a desktop and a phone.
//   BASE=http://127.0.0.1:8077 SHOTS=/tmp/shots node web/e2e/monitoring.e2e.mjs
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://127.0.0.1:8077";
const SHOTS = process.env.SHOTS ?? "/tmp/monitoring-shots";
mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
const errors = [];

for (const [width, name] of [[1360, "desktop"], [390, "phone"]]) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("console", (m) => m.type() === "error" && !/status of (401|403|404)/.test(m.text()) && errors.push(m.text()));
  await p.goto(`${BASE}/app/`);
  await p.getByLabel("Organisation code").fill("NG-AB");
  await p.getByLabel("Login (development sign-in)").fill("dps@abia.example");
  await p.getByRole("button", { name: "Sign in", exact: true }).click();
  await p.getByRole("link", { name: "Monitoring" }).click();
  await p.getByRole("heading", { name: "Government Monitoring Dashboard" }).waitFor();
  await p.getByText("Includes demonstration data.").waitFor();
  await p.getByText(/60 of [0-9,]+ learners/).waitFor();
  await p.getByRole("cell", { name: /TEST Pilot Girls Secondary School/ }).waitFor();
  await p.getByText("Not recorded yet").waitFor();
  for (const area of ["Attendance", "Academic performance", "Infrastructure", "Inspection reports"]) {
    await p.locator(".not-yet").getByText(area, { exact: true }).waitFor();
  }
  assert.equal(await p.locator(".dot").count(), 4, "the four pilot institutions with coordinates are placed");
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `no sideways scroll at ${width}px (${overflow}px)`);
  await p.screenshot({ path: `${SHOTS}/monitoring-${name}.png`, fullPage: true });
  await ctx.close();
}
await browser.close();
assert.deepEqual(errors, [], "no page errors");
console.log(`monitoring walk-through passed; screenshots in ${SHOTS}`);
