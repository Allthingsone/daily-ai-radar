"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { PUBLISH_RUN_TITLE, failedPublishAttempts, handleTimer } = require("../index.js");
const { evaluateGuard } = require("../../.github/scripts/daily-guard.cjs");

const GUARD_ENV = {
  GITHUB_TOKEN: "test-token", GITHUB_REPOSITORY: "owner/repo", GITHUB_REF_NAME: "main",
  GITHUB_RUN_ID: "99", GITHUB_RUN_ATTEMPT: "1", DIGEST_DATE: "2026-11-18", PHASE: "publish",
};
const TIMER_ENV = {
  GITHUB_ACTIONS_TOKEN: "test-token", GITHUB_OWNER: "owner", GITHUB_REPO: "repo",
  GITHUB_WORKFLOW: "pages.yml", GITHUB_REF: "main", PAGES_LATEST_URL: "https://example.com/latest.json",
};
const failure = (id, overrides = {}) => ({
  id, display_title: PUBLISH_RUN_TITLE, created_at: "2026-11-18T00:10:00Z",
  status: "completed", conclusion: "failure", run_attempt: 1, ...overrides,
});
const history = (runs) => async (url, options = {}) => {
  assert.notEqual(options.method, "POST");
  if (String(url).includes("latest.json")) return { ok: false, status: 404 };
  return { ok: true, status: 200, json: async () => ({ workflow_runs: runs }) };
};

test("news, replays, previous policies and previous days do not consume today's paper attempts", () => {
  const runs = [failure(1), failure(2, { run_attempt: 2 }),
    failure(3, { display_title: "Daily radar · previous-day papers · news" }),
    failure(4, { display_title: PUBLISH_RUN_TITLE + " · replay 2026-11-17" }),
    failure(5, { display_title: "Daily radar · publish" }),
    failure(6, { created_at: "2026-11-17T00:10:00Z" }),
    failure(7, { conclusion: "success" }), failure(8, { status: "in_progress" })];
  assert.equal(failedPublishAttempts(runs, "2026-11-18"), 3);
  assert.equal(failedPublishAttempts(runs, "2026-11-18", "2", 2), 2);
});

test("GitHub native fallback and Alibaba watchdog both stop after three failures", async () => {
  const fetcher = history([failure(1), failure(2), failure(3)]);
  const guard = await evaluateGuard(GUARD_ENV, fetcher);
  assert.equal(guard.shouldRun, false);
  assert.equal(guard.reason, "attempt-limit");
  const timer = await handleTimer({ triggerTime: "2026-11-18T01:00:00Z", payload: "publish" }, TIMER_ENV, fetcher);
  assert.equal(timer.reason, "attempt-limit");
});

test("two failures still permit the third attempt, and rerunning one job cannot bypass the limit", async () => {
  assert.equal((await evaluateGuard(GUARD_ENV, history([failure(1), failure(2)]))).shouldRun, true);
  const result = await evaluateGuard({ ...GUARD_ENV, GITHUB_RUN_ATTEMPT: "4" }, history([failure(99, { status: "in_progress" })]));
  assert.equal(result.shouldRun, false);
});

test("manual force, completed delivery and news need no history request", async () => {
  const noNetwork = async () => assert.fail("unexpected network call");
  assert.equal((await evaluateGuard({ ...GUARD_ENV, FORCE_RUN: "true" }, noNetwork)).shouldRun, true);
  assert.equal((await evaluateGuard({ ...GUARD_ENV, SUCCESS_CACHE_HIT: "true" }, noNetwork)).shouldRun, false);
  assert.equal((await evaluateGuard({ ...GUARD_ENV, PHASE: "news" }, noNetwork)).shouldRun, true);
});

test("pagination includes earlier failures and malformed history fails closed", async () => {
  let calls = 0;
  const result = await evaluateGuard(GUARD_ENV, async () => {
    calls += 1;
    return { ok: true, status: 200, json: async () => ({ workflow_runs: calls === 1
      ? Array.from({ length: 100 }, (_, i) => failure(i + 200, { display_title: "Daily radar · news" }))
      : [failure(1), failure(2), failure(3)] }) };
  });
  assert.equal(calls, 2);
  assert.equal(result.shouldRun, false);
  await assert.rejects(evaluateGuard(GUARD_ENV, async () => ({ ok: true, json: async () => ({}) })), /incomplete/);
});

test("today's paper attempt ceiling does not stop the news phase", async () => {
  const result = await handleTimer(
    { triggerTime: "2026-11-17T23:10:00Z", payload: '{"phase":"news","dry_run":true}' },
    TIMER_ENV, history([failure(1), failure(2), failure(3)]),
  );
  assert.equal(result.reason, "dry-run-would-dispatch");
});
