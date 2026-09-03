import assert from "node:assert/strict";
import test from "node:test";

import {
  NEWS_CRON,
  expectedArxivReleaseMinute,
  handleScheduled,
  mayStartPublish,
  phaseForCron,
  shanghaiDate,
} from "../src/index.js";

const ENV = {
  GITHUB_OWNER: "Allthingsone",
  GITHUB_REPO: "daily-ai-radar",
  GITHUB_WORKFLOW: "pages.yml",
  GITHUB_REF: "main",
  PAGES_LATEST_URL: "https://allthingsone.github.io/daily-ai-radar/data/latest.json",
  GITHUB_ACTIONS_TOKEN: "test-token",
};

test("cron routes news pre-screening separately from publication", () => {
  assert.equal(phaseForCron(NEWS_CRON), "news");
  assert.equal(phaseForCron("5,20,35,50 0 * * *"), "publish");
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
  const result = await handleScheduled(
    {
      cron: "5,20,35,50 0 * * *",
      scheduledTime: Date.parse("2026-12-03T00:50:00Z"),
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
  const result = await handleScheduled(
    {
      cron: "5,20,35,50 0 * * *",
      scheduledTime: Date.parse("2026-09-03T00:20:00Z"),
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

  const result = await handleScheduled(
    {
      cron: NEWS_CRON,
      scheduledTime: Date.parse("2026-09-02T23:35:00Z"),
    },
    ENV,
    fakeFetch,
  );

  assert.equal(result.reason, "phase-complete");
  assert.equal(calls, 2);
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

  const result = await handleScheduled(
    {
      cron: "5,20,35,50 0 * * *",
      scheduledTime: Date.parse("2026-09-03T00:05:00Z"),
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

  const result = await handleScheduled(
    {
      cron: "5,20,35,50 0 * * *",
      scheduledTime: Date.parse("2026-09-03T00:05:00Z"),
    },
    ENV,
    fakeFetch,
  );

  assert.equal(result.reason, "workflow-active");
  assert.equal(calls, 2);
});
