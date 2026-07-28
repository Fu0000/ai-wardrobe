import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/services/api";
import { getJob, type Job } from "@/services/jobs";
import { useAuthStore } from "@/stores/auth";
import { useJobStore } from "@/stores/jobs";

vi.mock("@/services/jobs", () => ({
  getJob: vi.fn(),
}));

const getJobMock = vi.mocked(getJob);
const storage = new Map<string, unknown>();

function authenticated() {
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.accessToken = "access-token";
  auth.expiresAt = Date.now() + 60_000;
  auth.userId = "user-1";
}

function job(status: Job["status"]): Job {
  return {
    id: "job-1",
    task_type: "STYLE_DIAGNOSIS",
    status,
    progress: status === "COMPLETED" ? 100 : 25,
    retry_count: 0,
    error_code: null,
    user_message: null,
    result_reference_type: null,
    result_reference_id: null,
    created_at: "2026-07-26T12:00:00Z",
    updated_at: "2026-07-26T12:00:10Z",
  };
}

describe("job recovery polling", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    storage.clear();
    getJobMock.mockReset();
    vi.stubGlobal("uni", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    authenticated();
  });

  it("treats a restored job without a snapshot as pending", () => {
    storage.set("aiw:tracked-jobs:v1", ["job-1"]);
    const jobs = useJobStore();

    jobs.hydrate();

    expect(jobs.hasPendingJobs).toBe(true);
  });

  it("keeps polling after a transient network failure", async () => {
    storage.set("aiw:tracked-jobs:v1", ["job-1"]);
    const jobs = useJobStore();
    jobs.hydrate();
    getJobMock.mockRejectedValueOnce(
      new ApiError("NETWORK_ERROR", "network unavailable", 0),
    );

    await jobs.refresh();

    expect(jobs.trackedJobIds).toEqual(["job-1"]);
    expect(jobs.hasPendingJobs).toBe(true);
  });

  it("stops polling only after a terminal result or confirmed 404", async () => {
    const jobs = useJobStore();
    jobs.track("job-1");
    getJobMock.mockResolvedValueOnce(job("COMPLETED"));

    await jobs.refresh();

    expect(jobs.hasPendingJobs).toBe(false);

    jobs.track("missing-job");
    getJobMock.mockRejectedValueOnce(
      new ApiError("JOB_NOT_FOUND", "not found", 404),
    );
    await jobs.refresh();

    expect(jobs.trackedJobIds).toEqual(["job-1"]);
    expect(jobs.hasPendingJobs).toBe(false);
  });
});
