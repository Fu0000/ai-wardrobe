import http from "k6/http";
import exec from "k6/execution";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import { Rate, Trend } from "k6/metrics";

const REQUIRED_CONFIRMATION = "I_ACCEPT_REAL_AI_COST_AND_AUTHORIZED_DATA";
const terminalStatuses = new Set([
  "COMPLETED",
  "FAILED_FINAL",
  "TIMED_OUT",
  "CANCELLED",
]);
const diagnosisSuccess = new Rate("diagnosis_success");
const diagnosisTotalLatency = new Trend("diagnosis_total_latency", true);
const correlationHeaders = new Rate("correlation_headers");
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function baseUrl() {
  const value = (__ENV.K6_BASE_URL || "").replace(/\/+$/, "");
  if (value.startsWith("https://")) {
    return value;
  }
  throw new Error("AI capacity tests require an HTTPS Staging base URL");
}

function integerEnv(name, fallback, minimum, maximum) {
  const value = __ENV[name] ? Number.parseInt(__ENV[name], 10) : fallback;
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return value;
}

if (__ENV.K6_ENABLE_COSTLY_AI_LOAD !== REQUIRED_CONFIRMATION) {
  throw new Error("explicit real AI cost and authorized-data confirmation is required");
}
if (__ENV.K6_EXPECTED_ENVIRONMENT !== "staging") {
  throw new Error("AI capacity tests are restricted to the Staging environment");
}
if (!__ENV.K6_RUN_ID || !/^[A-Za-z0-9_-]{8,64}$/.test(__ENV.K6_RUN_ID)) {
  throw new Error("K6_RUN_ID must be an 8-64 character unique identifier");
}
if (!__ENV.K6_DATA_FILE) {
  throw new Error("K6_DATA_FILE is required");
}

const target = baseUrl();
const pollTimeoutSeconds = integerEnv("K6_POLL_TIMEOUT_SECONDS", 90, 10, 600);
const dataset = new SharedArray("authorized-ai-capacity-dataset", () => {
  const payload = JSON.parse(open(__ENV.K6_DATA_FILE));
  if (!Array.isArray(payload) || ![10, 30, 50].includes(payload.length)) {
    throw new Error("capacity dataset must contain exactly 10, 30, or 50 records");
  }
  const tokens = new Set();
  return payload.map((record) => {
    if (
      typeof record.access_token !== "string" ||
      typeof record.asset_id !== "string" ||
      typeof record.occasion !== "string"
    ) {
      throw new Error("capacity dataset record is invalid");
    }
    if (tokens.has(record.access_token)) {
      throw new Error("capacity dataset requires one dedicated user per record");
    }
    tokens.add(record.access_token);
    return record;
  });
});

export function setup() {
  const response = http.get(`${target}/health/ready`, {
    tags: { operation: "staging_preflight" },
  });
  correlationHeaders.add(
    Boolean(response.headers["X-Request-Id"]) &&
      Boolean(response.headers["X-Trace-Id"]),
  );
  if (
    response.status !== 200 ||
    response.json("status") !== "ready" ||
    response.json("environment") !== "staging"
  ) {
    throw new Error("target did not prove Staging readiness");
  }
}

export const options = {
  discardResponseBodies: false,
  scenarios: {
    authorized_ai_jobs: {
      executor: "shared-iterations",
      vus: Math.min(integerEnv("K6_VUS", 10, 1, 50), dataset.length),
      iterations: dataset.length,
      maxDuration: "12m",
      gracefulStop: "30s",
    },
  },
  thresholds: {
    diagnosis_success: ["rate>0.94"],
    correlation_headers: ["rate==1"],
    diagnosis_total_latency: ["p(90)<20000", "p(95)<30000"],
    "http_req_failed{scenario:authorized_ai_jobs}": [
      {
        threshold: "rate<0.02",
        abortOnFail: true,
        delayAbortEval: "15s",
      },
    ],
  },
};

export default function () {
  const index = exec.scenario.iterationInTest;
  const record = dataset[index];
  const headers = {
    Authorization: `Bearer ${record.access_token}`,
    "Content-Type": "application/json",
    "Idempotency-Key": `perf-${__ENV.K6_RUN_ID}-${index}`,
    "User-Agent": "ai-wardrobe-k6-ai-capacity/1.0",
  };
  const startedAt = Date.now();
  const created = http.post(
    `${target}/api/v1/style-diagnoses`,
    JSON.stringify({
      asset_id: record.asset_id,
      occasion: record.occasion,
    }),
    {
      headers,
      tags: { operation: "diagnosis_create" },
    },
  );
  correlationHeaders.add(
    Boolean(created.headers["X-Request-Id"]) &&
      Boolean(created.headers["X-Trace-Id"]),
  );
  if (!check(created, {
    "diagnosis accepted": (response) => response.status === 202,
  })) {
    diagnosisSuccess.add(false);
    return;
  }

  const diagnosisId = created.json("id");
  const jobId = created.json("job_id");
  if (!check(created, {
    "diagnosis is newly created": (response) =>
      response.json("reused") === false,
    "diagnosis identity is valid": () =>
      typeof diagnosisId === "string" &&
      uuidPattern.test(diagnosisId) &&
      typeof jobId === "string" &&
      uuidPattern.test(jobId),
    "quota reservation is visible": (response) =>
      Number.isInteger(response.json("quota_remaining")) &&
      response.json("quota_remaining") >= 0,
  })) {
    diagnosisSuccess.add(false);
    return;
  }
  const deadline = Date.now() + pollTimeoutSeconds * 1000;
  while (Date.now() < deadline) {
    sleep(1);
    const response = http.get(
      `${target}/api/v1/style-diagnoses/${diagnosisId}`,
      {
        headers: {
          Authorization: `Bearer ${record.access_token}`,
          "User-Agent": "ai-wardrobe-k6-ai-capacity/1.0",
        },
        tags: { operation: "diagnosis_poll" },
      },
    );
    correlationHeaders.add(
      Boolean(response.headers["X-Request-Id"]) &&
        Boolean(response.headers["X-Trace-Id"]),
    );
    if (response.status !== 200) {
      continue;
    }
    const jobStatus = response.json("job_status");
    if (!terminalStatuses.has(jobStatus)) {
      continue;
    }
    const stableIdentity = response.json("job_id") === jobId;
    const succeeded = jobStatus === "COMPLETED" && stableIdentity;
    diagnosisSuccess.add(succeeded);
    diagnosisTotalLatency.add(Date.now() - startedAt);
    check(response, {
      "diagnosis completed": () => succeeded,
      "job identity is stable": () => stableIdentity,
    });
    return;
  }
  diagnosisSuccess.add(false);
  diagnosisTotalLatency.add(Date.now() - startedAt);
}
