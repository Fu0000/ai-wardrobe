import { defineStore } from "pinia";

import { ApiError } from "@/services/api";
import {
  createOptimization,
  getOptimization,
  type Optimization,
} from "@/services/optimizations";
import { useAuthStore } from "@/stores/auth";
import { createJobBackedResourceStore } from "@/stores/job-backed-resource";

const STORAGE_KEY = "aiw:optimizations:v1";

interface PendingRequest {
  diagnosisId: string;
  maxChangeLevel: 1 | 2 | 3;
  idempotencyKey: string;
}

interface PersistedOptimizationState {
  activeOptimizationId: string | null;
  activeJobId: string | null;
  recentOptimizationId: string | null;
  optimizationIdsByJob: Record<string, string>;
  pendingRequest: PendingRequest | null;
}

interface OptimizationState extends PersistedOptimizationState {
  current: Optimization | null;
  submitting: boolean;
  refreshing: boolean;
  errorCode: string | null;
  errorMessage: string | null;
}

function newIdempotencyKey(): string {
  return `optimization-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") {
    return "网络断开了，请检查连接后继续。优化请求不会重复创建。";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "优化任务暂时无法创建，请稍后重试。";
}

const optimizationResources = createJobBackedResourceStore<
  OptimizationState,
  Optimization,
  PersistedOptimizationState
>({
  storageKey: STORAGE_KEY,
  resourceId: (optimization) => optimization.id,
  jobId: (optimization) => optimization.job_id,
  isComplete: (optimization) => optimization.job_status === "COMPLETED",
  restore: (state, persisted) => {
    state.activeOptimizationId = persisted.activeOptimizationId;
    state.activeJobId = persisted.activeJobId;
    state.recentOptimizationId = persisted.recentOptimizationId;
    state.optimizationIdsByJob = persisted.optimizationIdsByJob ?? {};
    state.pendingRequest = persisted.pendingRequest ?? null;
  },
  serialize: (state) => ({
    activeOptimizationId: state.activeOptimizationId,
    activeJobId: state.activeJobId,
    recentOptimizationId: state.recentOptimizationId,
    optimizationIdsByJob: state.optimizationIdsByJob,
    pendingRequest: state.pendingRequest,
  }),
  activeResourceId: (state, resourceId) => {
    state.activeOptimizationId = resourceId;
  },
  recentResourceId: (state, resourceId) => {
    state.recentOptimizationId = resourceId;
  },
  resourceIdsByJob: (state) => state.optimizationIdsByJob,
  updateResourceIdsByJob: (state, resourceIdsByJob) => {
    state.optimizationIdsByJob = resourceIdsByJob;
  },
});

export const useOptimizationStore = defineStore("optimizations", {
  state: (): OptimizationState => ({
    activeOptimizationId: null,
    activeJobId: null,
    recentOptimizationId: null,
    optimizationIdsByJob: {},
    pendingRequest: null,
    current: null,
    submitting: false,
    refreshing: false,
    errorCode: null,
    errorMessage: null,
  }),
  actions: {
    hydrate() {
      optimizationResources.hydrate(this);
    },
    async create(
      diagnosisId: string,
      forceNew = false,
      maxChangeLevel: 1 | 2 | 3 = 3,
    ): Promise<Optimization> {
      if (this.submitting) {
        throw new Error("优化任务正在创建，请稍候。");
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        throw new Error("登录失败，请检查网络后重试。");
      }
      if (
        forceNew ||
        !this.pendingRequest ||
        this.pendingRequest.diagnosisId !== diagnosisId ||
        this.pendingRequest.maxChangeLevel !== maxChangeLevel
      ) {
        this.pendingRequest = {
          diagnosisId,
          maxChangeLevel,
          idempotencyKey: newIdempotencyKey(),
        };
      }
      this.persist();
      this.submitting = true;
      this.errorCode = null;
      this.errorMessage = null;
      try {
        const optimization = await createOptimization(
          diagnosisId,
          this.pendingRequest.idempotencyKey,
          auth.accessToken,
          maxChangeLevel,
        );
        this.accept(optimization);
        this.pendingRequest = null;
        this.persist();
        return optimization;
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
        this.persist();
        throw error;
      } finally {
        this.submitting = false;
      }
    },
    async refresh(optimizationId?: string): Promise<Optimization | null> {
      const targetId = optimizationId ?? this.activeOptimizationId;
      if (!targetId || this.refreshing) {
        return this.current;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        this.errorCode = "AUTH_REQUIRED";
        this.errorMessage = "登录失败，请检查网络后重试。";
        return null;
      }
      this.refreshing = true;
      this.errorCode = null;
      this.errorMessage = null;
      try {
        const optimization = await getOptimization(targetId, auth.accessToken);
        this.accept(optimization);
        return optimization;
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
        return null;
      } finally {
        this.refreshing = false;
      }
    },
    accept(optimization: Optimization) {
      optimizationResources.accept(this, optimization);
    },
    optimizationIdForJob(jobId: string): string | null {
      return optimizationResources.resourceIdForJob(this, jobId);
    },
    persist() {
      optimizationResources.persist(this);
    },
  },
});
