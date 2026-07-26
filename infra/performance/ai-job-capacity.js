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
  if (!Array.isArray(payload) || payload.length < 1 || payload.length > 200) {
    throw new Error("capacity dataset must contain 1-200 records");
  }
  return payload.map((record) => {
    if (
      typeof record.access_token !== "string" ||
      typeof record.asset_id !== "string" ||
      typeof record.occasion !== "string"
    ) {
      throw new Error("capacity dataset record is invalid");
    }
    return record;
  });
});

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
    diagnosis_total_latency: ["p(90)<20000", "p(95)<30000"],
    "http_req_failed{scenario:authorized_ai_jobs}": ["rate<0.05"],
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
  if (!check(created, {
    "diagnosis accepted": (response) => response.status === 202,
  })) {
    diagnosisSuccess.add(false);
    return;
  }

  const diagnosisId = created.json("id");
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
    if (response.status !== 200) {
      continue;
    }
    const jobStatus = response.json("job_status");
    if (!terminalStatuses.has(jobStatus)) {
      continue;
    }
    const succeeded = jobStatus === "COMPLETED";
    diagnosisSuccess.add(succeeded);
    diagnosisTotalLatency.add(Date.now() - startedAt);
    check(response, {
      "diagnosis completed": () => succeeded,
      "trace ID is present": (result) => Boolean(result.headers["X-Trace-Id"]),
    });
    return;
  }
  diagnosisSuccess.add(false);
  diagnosisTotalLatency.add(Date.now() - startedAt);
}
