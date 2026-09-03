const SHANGHAI_TIMEZONE = "Asia/Shanghai";
const NEW_YORK_TIMEZONE = "America/New_York";

export const NEWS_CRON = "15,35,55 23 * * *";

function requireSetting(env, name) {
  const value = String(env[name] || "").trim();
  if (!value) {
    throw new Error(`Missing Cloudflare Worker setting: ${name}`);
  }
  return value;
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

export function shanghaiDate(date) {
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

export function expectedArxivReleaseMinute(date) {
  // arXiv's 20:00 US Eastern announcement is 08:00 Shanghai during
  // daylight-saving time and 09:00 during standard time.
  const raw = 20 * 60 - newYorkOffsetMinutes(date) + 8 * 60;
  return ((raw % (24 * 60)) + 24 * 60) % (24 * 60);
}

export function mayStartPublish(date) {
  const weekday = shanghaiWeekday(date);
  if (weekday === "Sat" || weekday === "Sun") {
    // arXiv has no new daily announcement on Shanghai weekends. Publishing
    // the verified news-only digest is therefore safe at the first check.
    return true;
  }
  return shanghaiMinuteOfDay(date) >= expectedArxivReleaseMinute(date);
}

export function phaseForCron(cron) {
  return cron === NEWS_CRON ? "news" : "publish";
}

async function pageAlreadyPublished(env, now, fetchImpl) {
  const latestUrl = new URL(requireSetting(env, "PAGES_LATEST_URL"));
  latestUrl.searchParams.set("watchdog", String(now.getTime()));
  const response = await fetchImpl(latestUrl, {
    headers: { Accept: "application/json" },
    cf: { cacheTtl: 0 },
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
    "User-Agent": "daily-ai-radar-cloudflare-dispatcher",
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
  const response = await fetchImpl(
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
  const response = await fetchImpl(githubWorkflowUrl(env, "/dispatches"), {
    method: "POST",
    headers: githubHeaders(env),
    body: JSON.stringify({
      ref: requireSetting(env, "GITHUB_REF"),
      inputs: { phase, force: "false" },
    }),
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(
      `GitHub workflow dispatch failed with HTTP ${response.status}: ${detail}`,
    );
  }
}

export async function handleScheduled(event, env, fetchImpl = fetch) {
  const now = new Date(event.scheduledTime || Date.now());
  const phase = phaseForCron(event.cron);
  const localDate = shanghaiDate(now);

  if (phase === "publish" && !mayStartPublish(now)) {
    return { action: "skip", reason: "arxiv-not-ready", phase, localDate };
  }

  try {
    if (await pageAlreadyPublished(env, now, fetchImpl)) {
      return { action: "skip", reason: "already-published", phase, localDate };
    }
  } catch (error) {
    // A stale/unreachable Pages CDN must not prevent the GitHub API checks and
    // a needed dispatch. GitHub run state plus the workflow cache remain the
    // authoritative duplicate guards.
    console.warn(String(error));
  }

  const runs = await workflowRuns(env, fetchImpl);
  if (completedPhaseToday(runs, phase, localDate)) {
    return { action: "skip", reason: "phase-complete", phase, localDate };
  }
  if (activeRunExists(runs)) {
    return { action: "skip", reason: "workflow-active", phase, localDate };
  }

  await dispatchWorkflow(env, phase, fetchImpl);
  return { action: "dispatch", phase, localDate };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      handleScheduled(event, env).then((result) => {
        console.log(JSON.stringify(result));
      }),
    );
  },
};
