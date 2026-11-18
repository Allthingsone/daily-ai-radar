"use strict";

const { appendFileSync } = require("node:fs");
const { MAX_PUBLISH_ATTEMPTS, failedPublishAttempts, workflowRuns } = require("../../aliyun-fc/index.js");

async function evaluateGuard(env, fetchImpl = fetch) {
  if (env.FORCE_RUN === "true") return { shouldRun: true, reason: "manual-force" };
  if (env.SUCCESS_CACHE_HIT === "true") return { shouldRun: false, reason: "already-delivered" };
  if (env.PHASE === "news") return { shouldRun: true, reason: "news-phase" };
  const [owner, repo] = env.GITHUB_REPOSITORY.split("/");
  const runs = await workflowRuns({
    GITHUB_ACTIONS_TOKEN: env.GITHUB_TOKEN,
    GITHUB_OWNER: owner, GITHUB_REPO: repo,
    GITHUB_WORKFLOW: "pages.yml", GITHUB_REF: env.GITHUB_REF_NAME,
  }, fetchImpl, env.DIGEST_DATE);
  const attempts = failedPublishAttempts(runs, env.DIGEST_DATE, env.GITHUB_RUN_ID, env.GITHUB_RUN_ATTEMPT);
  return { shouldRun: attempts < MAX_PUBLISH_ATTEMPTS, reason: attempts < MAX_PUBLISH_ATTEMPTS ? "retry-available" : "attempt-limit", attempts };
}

if (require.main === module) {
  evaluateGuard(process.env).then((result) => {
    appendFileSync(process.env.GITHUB_OUTPUT, `should_run=${result.shouldRun}\n`);
    if (!result.shouldRun && result.reason === "attempt-limit") {
      appendFileSync(process.env.GITHUB_STEP_SUMMARY,
        "Previous-day paper delivery reached its 3-attempt limit. Automatic retries are stopped for this digest date. The failure remains unresolved; a manual force run can retry after investigation.\n");
    }
    console.log(JSON.stringify(result));
  }).catch((error) => { console.error(error.message); process.exitCode = 1; });
}

module.exports = { evaluateGuard };
