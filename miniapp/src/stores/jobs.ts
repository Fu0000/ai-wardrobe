import { defineStore } from "pinia";

import { ApiError } from "@/services/api";
import { getJob, type Job } from "@/services/jobs";
import { useAuthStore } from "@/stores/auth";

const STORAGE_KEY = "aiw:tracked-jobs:v1";
const MAX_TRACKED_JOBS = 20;
const TERMINAL_STATUSES = new Set([
  "COMPLETED",
  "FAILED_FINAL",
  "TIMED_OUT",
  "CANCELLED",
]);

interface JobState {
  trackedJobIds: string[];
  jobs: Record<string, Job>;
  refreshing: boolean;
  lastRefreshedAt: number | null;
}

export const useJobStore = defineStore("jobs", {
  state: (): JobState => ({
    trackedJobIds: [],
    jobs: {},
    refreshing: false,
    lastRefreshedAt: null,
  }),
  getters: {
    orderedJobs(state): Job[] {
      return state.trackedJobIds
        .map((id) => state.jobs[id])
        .filter((job): job is Job => Boolean(job));
    },
    hasPendingJobs(state): boolean {
      return state.trackedJobIds.some(
        (jobId) =>
          !state.jobs[jobId] ||
          !TERMINAL_STATUSES.has(state.jobs[jobId].status),
      );
    },
  },
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as string[] | "";
      if (Array.isArray(persisted)) {
        this.trackedJobIds = persisted.slice(0, MAX_TRACKED_JOBS);
      }
    },
    track(jobId: string) {
      this.trackedJobIds = [
        jobId,
        ...this.trackedJobIds.filter((id) => id !== jobId),
      ].slice(0, MAX_TRACKED_JOBS);
      this.persist();
    },
    async refresh() {
      if (this.refreshing || this.trackedJobIds.length === 0) {
        return;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      const accessToken = auth.accessToken;
      if (!accessToken) {
        return;
      }

      this.refreshing = true;
      try {
        await Promise.all(
          this.trackedJobIds.map(async (jobId) => {
            const existing = this.jobs[jobId];
            if (existing && TERMINAL_STATUSES.has(existing.status)) {
              return;
            }
            try {
              this.jobs[jobId] = await getJob(jobId, accessToken);
            } catch (error) {
              if (error instanceof ApiError && error.statusCode === 404) {
                this.untrack(jobId);
              }
            }
          }),
        );
        this.lastRefreshedAt = Date.now();
      } finally {
        this.refreshing = false;
      }
    },
    untrack(jobId: string) {
      this.trackedJobIds = this.trackedJobIds.filter((id) => id !== jobId);
      delete this.jobs[jobId];
      this.persist();
    },
    persist() {
      uni.setStorageSync(STORAGE_KEY, this.trackedJobIds);
    },
  },
});
