import http from "k6/http";
import { check } from "k6";
import { Rate } from "k6/metrics";

const businessSuccess = new Rate("business_success");

function integerEnv(name, fallback, minimum, maximum) {
  const value = __ENV[name] ? Number.parseInt(__ENV[name], 10) : fallback;
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return value;
}

function baseUrl() {
  const value = (__ENV.K6_BASE_URL || "").replace(/\/+$/, "");
  if (
    value.startsWith("https://") ||
    value.startsWith("http://localhost") ||
    value.startsWith("http://127.0.0.1")
  ) {
    return value;
  }
  throw new Error("K6_BASE_URL must use HTTPS except for localhost");
}

const target = baseUrl();
const token = __ENV.K6_ACCESS_TOKEN || "";
if (!token) {
  throw new Error("K6_ACCESS_TOKEN is required");
}

const startRate = integerEnv("K6_START_RATE", 5, 1, 200);
const targetRate = integerEnv("K6_TARGET_RATE", 20, startRate, 500);
const preAllocatedVUs = integerEnv("K6_PREALLOCATED_VUS", 20, 1, 500);

export const options = {
  discardResponseBodies: true,
  scenarios: {
    api_reads: {
      executor: "ramping-arrival-rate",
      startRate,
      timeUnit: "1s",
      preAllocatedVUs,
      maxVUs: Math.min(1000, preAllocatedVUs * 4),
      stages: [
        { target: targetRate, duration: "1m" },
        { target: targetRate, duration: "3m" },
        { target: 0, duration: "30s" },
      ],
      gracefulStop: "30s",
    },
  },
  thresholds: {
    business_success: ["rate>0.99"],
    "http_req_failed{scenario:api_reads}": ["rate<0.01"],
    "http_req_duration{scenario:api_reads}": ["p(95)<1000", "p(99)<2000"],
  },
};

const authenticatedParams = {
  headers: {
    Authorization: `Bearer ${token}`,
    "User-Agent": "ai-wardrobe-k6-read/1.0",
  },
  tags: { operation: "authenticated_read" },
};

export function setup() {
  const readiness = http.get(`${target}/health/ready`, {
    tags: { operation: "readiness" },
  });
  const profile = http.get(`${target}/api/v1/me/profile`, authenticatedParams);
  const valid = check(readiness, {
    "readiness is 200": (response) => response.status === 200,
  }) && check(profile, {
    "profile is 200": (response) => response.status === 200,
  });
  if (!valid) {
    throw new Error("preflight failed");
  }

  const paths = ["/api/v1/me/profile"];
  if (__ENV.K6_DIAGNOSIS_ID) {
    paths.push(`/api/v1/style-diagnoses/${__ENV.K6_DIAGNOSIS_ID}`);
  }
  if (__ENV.K6_OPTIMIZATION_ID) {
    paths.push(`/api/v1/style-optimizations/${__ENV.K6_OPTIMIZATION_ID}`);
  }
  if (__ENV.K6_SCENE_CODE) {
    paths.push(`/api/v1/shares/${__ENV.K6_SCENE_CODE}`);
  }
  return { paths };
}

export default function (data) {
  const path = data.paths[__ITER % data.paths.length];
  const response = http.get(`${target}${path}`, authenticatedParams);
  const succeeded = check(response, {
    "authenticated read succeeds": (result) => result.status === 200,
    "trace ID is present": (result) => Boolean(result.headers["X-Trace-Id"]),
  });
  businessSuccess.add(succeeded);
}
