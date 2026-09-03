"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  expectedArxivReleaseMinute,
  handleTimer,
  mayStartPublish,
  parseTimerEvent,
  requestFromTimerEvent,
  shanghaiDate,
} = require("../index.js");

const ENV = {
  GITHUB_OWNER: "Allthingsone",
  GITHUB_REPO: "daily-ai-radar",
  GITHUB_WORKFLOW: "pages.yml",
  GITHUB_REF: "main",
  PAGES_LATEST_URL: "https://allthingsone.github.io/daily-ai-radar/data/latest.json",
  GITHUB_ACTIONS_TOKEN: "test-token",
  HTTP_TIMEOUT_MS: "15000",
};

test("Alibaba timer event and JSON payload are parsed", () => {
  const event = parseTimerEvent(
    Buffer.from(
      JSON.stringify({
        triggerTime: "2026-09-02T23:15:00Z",
        triggerName: "daily-radar-news",
        payload: '{"phase":"news","dry_run":true}',
      }),
    ),
  );
  assert.deepEqual(requestFromTimerEvent(event), { phase: "news", dryRun: true });
  assert.deepEqual(requestFromTimerEvent({ payload: "publish" }), {
    phase: "publish",
    dryRun: false,
  });
});

test("unsupported or missing timer phases fail closed", () => {
  assert.throws(() => requestFromTimerEvent({ payload: "paper" }), /Timer payload/);
  assert.throws(
    () => requestFromTimerEvent({ payload: '{"dry_run":true}' }),
    /Unsupported timer phase/,
  );
});

test("Shanghai date conversion is independent of the UTC calendar day", () => {
  assert.equal(shanghaiDate(new Date("2026-09-02T23:15:00Z")), "2026-09-03");
});

test("arXiv readiness follows US daylight-saving time", () => {
  const summerBefore = new Date("2026-09-02T23:59:00Z");
  const summerReady = new Date("2026-09-03T00:05:00Z");
  const winterBefore = new Date("2026-12-03T00:50:00Z");
  const winterReady = new Date("2026-12-03T01:05:00Z");

  assert.equal(expectedArxivReleaseMinute(summerReady), 8 * 60);
  assert.equal(mayStartPublish(summerBefore), false);
  assert.equal(mayStartPublish(summerReady), true);
  assert.equal(expectedArxivReleaseMinute(winterReady), 9 * 60);
  assert.equal(mayStartPublish(winterBefore), false);
  assert.equal(mayStartPublish(winterReady), true);
});

test("weekends can publish a truthful news-only digest at the first check", () => {
  assert.equal(mayStartPublish(new Date("2026-09-04T23:05:00Z")), true);
});

test("watchdog makes no network call before the winter arXiv boundary", async () => {
  let called = false;
  const result = await handleTimer(
    {
      triggerTime: "2026-12-03T00:50:00Z",
      payload: '{"phase":"publish"}',
    },
    ENV,
    async () => {
      called = true;
    },
  );

  assert.equal(result.reason, "arxiv-not-ready");
  assert.equal(called, false);
});

test("watchdog stops when Pages already contains today's digest", async () => {
  let calls = 0;
  const result = await handleTimer(
    {
      triggerTime: "2026-09-03T00:20:00Z",
      payload: '{"phase":"publish"}',
    },
    ENV,
    async () => {
      calls += 1;
      return {
        ok: true,
        status: 200,
        json: async () => ({ generated_at: "2026-09-03T00:10:00Z" }),
      };
    },
  );

  assert.equal(result.reason, "already-published");
  assert.equal(calls, 1);
});

test("successful news pre-screen suppresses later news retry", async () => {
  let calls = 0;
  const fakeFetch = async (url) => {
    calls += 1;
    if (String(url).includes("latest.json")) {
      return { ok: false, status: 404 };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        workflow_runs: [
          {
            status: "completed",
            conclusion: "success",
            display_title: "Daily radar · news",
            created_at: "2026-09-02T23:16:00Z",
          },
        ],
      }),
    };
  };

  const result = await handleTimer(
    {
      triggerTime: "2026-09-02T23:35:00Z",
      payload: '{"phase":"news"}',
    },
    ENV,
    fakeFetch,
  );

  assert.equal(result.reason, "phase-complete");
  assert.equal(calls, 2);
});

test("safe dry-run checks state but never posts a dispatch", async () => {
  const calls = [];
  const fakeFetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).includes("latest.json")) {
      return { ok: false, status: 404 };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ workflow_runs: [] }),
    };
  };

  const result = await handleTimer(
    {
      triggerTime: "2026-09-02T23:15:00Z",
      payload: '{"phase":"news","dry_run":true}',
    },
    ENV,
    fakeFetch,
  );

  assert.equal(result.reason, "dry-run-would-dispatch");
  assert.equal(calls.length, 2);
  assert.equal(calls.some((call) => call.options.method === "POST"), false);
});

test("watchdog dispatches the requested phase when no run is active", async () => {
  const calls = [];
  const fakeFetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).includes("latest.json")) {
      return { ok: false, status: 404 };
    }
    if (String(url).includes("/runs?")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ workflow_runs: [] }),
      };
    }
    return { ok: true, status: 200, text: async () => "" };
  };

  const result = await handleTimer(
    {
      triggerTime: "2026-09-03T00:05:00Z",
      payload: '{"phase":"publish"}',
    },
    ENV,
    fakeFetch,
  );

  assert.equal(result.action, "dispatch");
  assert.equal(calls.length, 3);
  assert.equal(calls[2].options.method, "POST");
  assert.equal(calls[2].options.headers["X-GitHub-Api-Version"], "2026-03-10");
  assert.deepEqual(JSON.parse(calls[2].options.body).inputs, {
    phase: "publish",
    force: "false",
  });
});

test("watchdog does not dispatch while this workflow is active", async () => {
  let calls = 0;
  const fakeFetch = async (url) => {
    calls += 1;
    if (String(url).includes("latest.json")) {
      return { ok: false, status: 404 };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        workflow_runs: [{ status: "in_progress", created_at: "2026-09-03T00:00:00Z" }],
      }),
    };
  };

  const result = await handleTimer(
    {
      triggerTime: "2026-09-03T00:05:00Z",
      payload: '{"phase":"publish"}',
    },
    ENV,
    fakeFetch,
  );

  assert.equal(result.reason, "workflow-active");
  assert.equal(calls, 2);
});

test("missing token is reported by name without exposing any value", async () => {
  const env = { ...ENV };
  delete env.GITHUB_ACTIONS_TOKEN;
  await assert.rejects(
    handleTimer({ payload: "news" }, env, async () => undefined),
    /GITHUB_ACTIONS_TOKEN/,
  );
});
