"use strict";

const SHANGHAI_TIMEZONE = "Asia/Shanghai";
const VALID_PHASES = new Set(["news", "publish", "auto"]);
const AUTO_WINDOW_START_MINUTE = 7 * 60;
const AUTO_WINDOW_END_MINUTE = 10 * 60 + 30;
const PUBLISH_START_MINUTE = 8 * 60;
const MAX_PUBLISH_ATTEMPTS = 3;
const PUBLISH_RUN_TITLE = "Daily radar · previous-day papers · publish";
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
          'Timer payload must be "news", "publish", "auto", or JSON such as {"phase":"auto"}',
        );
      }
    }
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(
      'Timer payload must be "news", "publish", "auto", or JSON such as {"phase":"auto"}',
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

function resolveAutomaticPhase(date) {
  const minute = shanghaiMinuteOfDay(date);
  if (minute < AUTO_WINDOW_START_MINUTE || minute > AUTO_WINDOW_END_MINUTE) {
    return null;
  }
  // News still starts at 07:00; yesterday's papers do not wait for today's release.
  return minute < PUBLISH_START_MINUTE ? "news" : "publish";
}

function mayStartPublish(date) {
  return shanghaiMinuteOfDay(date) >= PUBLISH_START_MINUTE;
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

async function workflowRuns(env, fetchImpl, localDate) {
  const branch = encodeURIComponent(requireSetting(env, "GITHUB_REF"));
  const runs = [];
  for (let page = 1; ; page += 1) {
    const response = await fetchWithTimeout(
      fetchImpl, env,
      githubWorkflowUrl(env, `/runs?branch=${branch}&per_page=100&page=${page}`),
      { headers: githubHeaders(env) },
    );
    if (!response.ok) throw new Error(`GitHub workflow-runs check failed with HTTP ${response.status}`);
    const payload = await response.json();
    if (!Array.isArray(payload.workflow_runs)) throw new Error("GitHub workflow-runs response is incomplete");
    runs.push(...payload.workflow_runs);
    if (payload.workflow_runs.length < 100 || !localDate || payload.workflow_runs.some((run) => {
      const created = new Date(run.created_at || "");
      return !Number.isNaN(created.getTime()) && shanghaiDate(created) < localDate;
    })) return runs;
    if (page >= 10) throw new Error("Unable to verify today's full workflow history");
  }
}

function failedPublishAttempts(runs, localDate, currentRunId = "", currentRunAttempt = 1) {
  return Math.max(0, Number(currentRunAttempt) - 1) + runs.reduce((count, run) => {
    if (String(run.id) === String(currentRunId) || String(run.display_title || "").trim() !== PUBLISH_RUN_TITLE
        || run.status !== "completed" || !["failure", "timed_out"].includes(run.conclusion)) return count;
    const started = new Date(run.run_started_at || run.created_at || "");
    if (Number.isNaN(started.getTime()) || shanghaiDate(started) !== localDate) return count;
    return count + Math.max(1, Number(run.run_attempt) || 1);
  }, 0);
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
  const { phase: requestedPhase, dryRun } = requestFromTimerEvent(timerEvent);
  const now = new Date(timerEvent.triggerTime || Date.now());
  if (Number.isNaN(now.getTime())) {
    throw new Error(`Invalid timer triggerTime: ${timerEvent.triggerTime}`);
  }
  const localDate = shanghaiDate(now);
  const phase = requestedPhase === "auto" ? resolveAutomaticPhase(now) : requestedPhase;
  if (!phase) {
    return {
      action: "skip",
      reason: "outside-window",
      phase: requestedPhase,
      localDate,
      dryRun,
    };
  }
  const requestMetadata = requestedPhase === "auto" ? { requestedPhase } : {};

  if (phase === "publish" && !mayStartPublish(now)) {
    return {
      action: "skip",
      reason: "before-publish-window",
      phase,
      localDate,
      dryRun,
      ...requestMetadata,
    };
  }

  try {
    if (await pageAlreadyPublished(env, now, fetchImpl)) {
      return {
        action: "skip",
        reason: "already-published",
        phase,
        localDate,
        dryRun,
        ...requestMetadata,
      };
    }
  } catch (error) {
    // An unreachable Pages CDN must not suppress the GitHub-side duplicate
    // guards or a needed dispatch.
    console.warn(String(error));
  }

  const runs = await workflowRuns(env, fetchImpl, localDate);
  if (completedPhaseToday(runs, phase, localDate)) {
    return {
      action: "skip",
      reason: "phase-complete",
      phase,
      localDate,
      dryRun,
      ...requestMetadata,
    };
  }
  if (activeRunExists(runs)) {
    return {
      action: "skip",
      reason: "workflow-active",
      phase,
      localDate,
      dryRun,
      ...requestMetadata,
    };
  }
  if (phase === "publish" && failedPublishAttempts(runs, localDate) >= MAX_PUBLISH_ATTEMPTS) {
    return { action: "skip", reason: "attempt-limit", phase, localDate, dryRun, ...requestMetadata };
  }
  if (dryRun) {
    return {
      action: "skip",
      reason: "dry-run-would-dispatch",
      phase,
      localDate,
      dryRun,
      ...requestMetadata,
    };
  }

  await dispatchWorkflow(env, phase, fetchImpl);
  return { action: "dispatch", phase, localDate, dryRun, ...requestMetadata };
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
  MAX_PUBLISH_ATTEMPTS,
  PUBLISH_RUN_TITLE,
  failedPublishAttempts,
  workflowRuns,
  handleTimer,
  handler,
  mayStartPublish,
  parseTimerEvent,
  requestFromTimerEvent,
  resolveAutomaticPhase,
  shanghaiDate,
};
