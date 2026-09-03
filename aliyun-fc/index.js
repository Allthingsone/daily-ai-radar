"use strict";

const SHANGHAI_TIMEZONE = "Asia/Shanghai";
const NEW_YORK_TIMEZONE = "America/New_York";
const VALID_PHASES = new Set(["news", "publish"]);
const REQUIRED_ENVIRONMENT = [
  "GITHUB_ACTIONS_TOKEN",
  "GITHUB_OWNER",
  "GITHUB_REPO",
  "GITHUB_WORKFLOW",
  "GITHUB_REF",
  "PAGES_LATEST_URL",
];

function requireSetting(env, name) {
  const value = String(env[name] || "").trim();
  if (!value) {
    throw new Error(`Missing Alibaba Function Compute environment variable: ${name}`);
  }
  return value;
}

function validateEnvironment(env) {
  for (const name of REQUIRED_ENVIRONMENT) {
    requireSetting(env, name);
  }
}

function parseTimerEvent(event) {
  if (Buffer.isBuffer(event)) {
    event = event.toString("utf8");
  }
  if (typeof event === "string") {
    const text = event.trim();
    if (!text) {
      throw new Error("Alibaba Function Compute timer event is empty");
    }
    try {
      event = JSON.parse(text);
    } catch (error) {
      throw new Error(`Alibaba Function Compute timer event is not valid JSON: ${error.message}`);
    }
  }
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    throw new Error("Alibaba Function Compute timer event must be a JSON object");
  }
  return event;
}

function booleanValue(value) {
  if (typeof value === "boolean") {
    return value;
  }
  return ["1", "true", "yes", "on"].includes(String(value || "").trim().toLowerCase());
}

function requestFromTimerEvent(timerEvent) {
  let payload = timerEvent.payload;
  if (typeof payload === "string") {
    const text = payload.trim();
    if (VALID_PHASES.has(text)) {
      payload = { phase: text };
    } else {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        throw new Error(
          'Timer payload must be "news", "publish", or JSON such as {"phase":"news"}',
        );
      }
    }
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(
      'Timer payload must be "news", "publish", or JSON such as {"phase":"news"}',
    );
  }

  const phase = String(payload.phase || "").trim().toLowerCase();
  if (!VALID_PHASES.has(phase)) {
    throw new Error(`Unsupported timer phase: ${phase || "missing"}`);
  }
  return { phase, dryRun: booleanValue(payload.dry_run) };
}

function dateTimeParts(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(
    parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
  );
}

function shanghaiDate(date) {
  const parts = dateTimeParts(date, SHANGHAI_TIMEZONE);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function shanghaiMinuteOfDay(date) {
  const parts = dateTimeParts(date, SHANGHAI_TIMEZONE);
  return Number(parts.hour) * 60 + Number(parts.minute);
}

function shanghaiWeekday(date) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: SHANGHAI_TIMEZONE,
    weekday: "short",
  }).format(date);
}

function newYorkOffsetMinutes(date) {
  const name = new Intl.DateTimeFormat("en-US", {
    timeZone: NEW_YORK_TIMEZONE,
    timeZoneName: "longOffset",
  })
    .formatToParts(date)
    .find((part) => part.type === "timeZoneName")?.value;
  const match = /^GMT([+-])(\d{1,2})(?::(\d{2}))?$/.exec(name || "");
  if (!match) {
    throw new Error(`Unable to resolve New York UTC offset: ${name || "unknown"}`);
  }
  const sign = match[1] === "+" ? 1 : -1;
  return sign * (Number(match[2]) * 60 + Number(match[3] || 0));
}

function expectedArxivReleaseMinute(date) {
  // arXiv announces at 20:00 US Eastern: 08:00 Shanghai during daylight
  // saving time and 09:00 during standard time.
  const raw = 20 * 60 - newYorkOffsetMinutes(date) + 8 * 60;
  return ((raw % (24 * 60)) + 24 * 60) % (24 * 60);
}

function mayStartPublish(date) {
  const weekday = shanghaiWeekday(date);
  if (weekday === "Sat" || weekday === "Sun") {
    // There is no new arXiv daily announcement on Shanghai weekends, so a
    // truthful news-only digest can be published at the first check.
    return true;
  }
  return shanghaiMinuteOfDay(date) >= expectedArxivReleaseMinute(date);
}

function timeoutSignal(env) {
  const value = Number(String(env.HTTP_TIMEOUT_MS || "15000"));
  if (!Number.isFinite(value) || value < 1000 || value > 25000) {
    throw new Error("HTTP_TIMEOUT_MS must be between 1000 and 25000");
  }
  return AbortSignal.timeout(value);
}

function fetchWithTimeout(fetchImpl, env, url, options = {}) {
  return fetchImpl(url, { ...options, signal: timeoutSignal(env) });
}

async function pageAlreadyPublished(env, now, fetchImpl) {
  const latestUrl = new URL(requireSetting(env, "PAGES_LATEST_URL"));
  latestUrl.searchParams.set("watchdog", String(now.getTime()));
  const response = await fetchWithTimeout(fetchImpl, env, latestUrl, {
    headers: { Accept: "application/json", "Cache-Control": "no-cache" },
    cache: "no-store",
  });
  if (response.status === 404) {
    return false;
  }
  if (!response.ok) {
    throw new Error(`Pages freshness check failed with HTTP ${response.status}`);
  }
  const payload = await response.json();
  const generatedAt = new Date(payload.generated_at || "");
  return !Number.isNaN(generatedAt.getTime()) && shanghaiDate(generatedAt) === shanghaiDate(now);
}

function githubHeaders(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${requireSetting(env, "GITHUB_ACTIONS_TOKEN")}`,
    "Content-Type": "application/json",
    "X-GitHub-Api-Version": "2026-03-10",
    "User-Agent": "daily-ai-radar-aliyun-fc-dispatcher",
  };
}

function githubWorkflowUrl(env, suffix = "") {
  const owner = encodeURIComponent(requireSetting(env, "GITHUB_OWNER"));
  const repo = encodeURIComponent(requireSetting(env, "GITHUB_REPO"));
  const workflow = encodeURIComponent(requireSetting(env, "GITHUB_WORKFLOW"));
  return `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}${suffix}`;
}

async function workflowRuns(env, fetchImpl) {
  const branch = encodeURIComponent(requireSetting(env, "GITHUB_REF"));
  const response = await fetchWithTimeout(
    fetchImpl,
    env,
    githubWorkflowUrl(env, `/runs?branch=${branch}&per_page=50`),
    { headers: githubHeaders(env) },
  );
  if (!response.ok) {
    throw new Error(`GitHub workflow-runs check failed with HTTP ${response.status}`);
  }
  const payload = await response.json();
  return Array.isArray(payload.workflow_runs) ? payload.workflow_runs : [];
}

function isRunFromShanghaiDate(run, localDate) {
  const createdAt = new Date(run.created_at || "");
  return !Number.isNaN(createdAt.getTime()) && shanghaiDate(createdAt) === localDate;
}

function completedPhaseToday(runs, phase, localDate) {
  const suffix = `· ${phase}`;
  return runs.some(
    (run) =>
      run.status === "completed" &&
      run.conclusion === "success" &&
      String(run.display_title || "").trim().endsWith(suffix) &&
      isRunFromShanghaiDate(run, localDate),
  );
}

function activeRunExists(runs) {
  return runs.some((run) => run.status && run.status !== "completed");
}

async function dispatchWorkflow(env, phase, fetchImpl) {
  const response = await fetchWithTimeout(
    fetchImpl,
    env,
    githubWorkflowUrl(env, "/dispatches"),
    {
      method: "POST",
      headers: githubHeaders(env),
      body: JSON.stringify({
        ref: requireSetting(env, "GITHUB_REF"),
        inputs: { phase, force: "false" },
      }),
    },
  );
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub workflow dispatch failed with HTTP ${response.status}: ${detail}`);
  }
}

async function handleTimer(event, env, fetchImpl = fetch) {
  validateEnvironment(env);
  const timerEvent = parseTimerEvent(event);
  const { phase, dryRun } = requestFromTimerEvent(timerEvent);
  const now = new Date(timerEvent.triggerTime || Date.now());
  if (Number.isNaN(now.getTime())) {
    throw new Error(`Invalid timer triggerTime: ${timerEvent.triggerTime}`);
  }
  const localDate = shanghaiDate(now);

  if (phase === "publish" && !mayStartPublish(now)) {
    return { action: "skip", reason: "arxiv-not-ready", phase, localDate, dryRun };
  }

  try {
    if (await pageAlreadyPublished(env, now, fetchImpl)) {
      return { action: "skip", reason: "already-published", phase, localDate, dryRun };
    }
  } catch (error) {
    // An unreachable Pages CDN must not suppress the GitHub-side duplicate
    // guards or a needed dispatch.
    console.warn(String(error));
  }

  const runs = await workflowRuns(env, fetchImpl);
  if (completedPhaseToday(runs, phase, localDate)) {
    return { action: "skip", reason: "phase-complete", phase, localDate, dryRun };
  }
  if (activeRunExists(runs)) {
    return { action: "skip", reason: "workflow-active", phase, localDate, dryRun };
  }
  if (dryRun) {
    return { action: "skip", reason: "dry-run-would-dispatch", phase, localDate, dryRun };
  }

  await dispatchWorkflow(env, phase, fetchImpl);
  return { action: "dispatch", phase, localDate, dryRun };
}

async function handler(event, context) {
  const result = await handleTimer(event, process.env, fetch);
  console.log(
    JSON.stringify({
      ...result,
      requestId: context && context.requestId ? context.requestId : undefined,
    }),
  );
  return result;
}

module.exports = {
  REQUIRED_ENVIRONMENT,
  expectedArxivReleaseMinute,
  handleTimer,
  handler,
  mayStartPublish,
  parseTimerEvent,
  requestFromTimerEvent,
  shanghaiDate,
};
