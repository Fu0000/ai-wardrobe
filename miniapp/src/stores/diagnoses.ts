import { defineStore } from "pinia";

import { ApiError } from "@/services/api";
import {
  createDiagnosis,
  getDiagnosis,
  type Diagnosis,
  type Occasion,
} from "@/services/diagnoses";
import { useAuthStore } from "@/stores/auth";
import { useJobStore } from "@/stores/jobs";

const STORAGE_KEY = "aiw:diagnoses:v1";
const MAX_JOB_MAPPINGS = 20;

interface PendingRequest {
  identity: string;
  assetId: string;
  occasion: Occasion;
  idempotencyKey: string;
}

interface PersistedDiagnosisState {
  activeDiagnosisId: string | null;
  activeJobId: string | null;
  recentDiagnosisId: string | null;
  diagnosisIdsByJob: Record<string, string>;
  pendingRequest: PendingRequest | null;
}

interface DiagnosisState extends PersistedDiagnosisState {
  current: Diagnosis | null;
  submitting: boolean;
  refreshing: boolean;
  errorCode: string | null;
  errorMessage: string | null;
}

function newIdempotencyKey(): string {
  return `diagnosis-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") {
    return "网络断开了，请检查连接后继续。请求不会重复创建。";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "诊断暂时无法创建，请稍后重试。";
}

export const useDiagnosisStore = defineStore("diagnoses", {
  state: (): DiagnosisState => ({
    activeDiagnosisId: null,
    activeJobId: null,
    recentDiagnosisId: null,
    diagnosisIdsByJob: {},
    pendingRequest: null,
    current: null,
    submitting: false,
    refreshing: false,
    errorCode: null,
    errorMessage: null,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as
        | PersistedDiagnosisState
        | "";
      if (!persisted) {
        return;
      }
      this.activeDiagnosisId = persisted.activeDiagnosisId;
      this.activeJobId = persisted.activeJobId;
      this.recentDiagnosisId = persisted.recentDiagnosisId;
      this.diagnosisIdsByJob = persisted.diagnosisIdsByJob ?? {};
      this.pendingRequest = persisted.pendingRequest;
    },
    async create(
      assetId: string,
      occasion: Occasion,
      forceNew = false,
    ): Promise<Diagnosis> {
      if (this.submitting) {
        throw new Error("诊断正在创建，请稍候。");
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        throw new Error("登录失败，请检查网络后重试。");
      }

      const identity = `${assetId}:${occasion}`;
      if (
        forceNew ||
        !this.pendingRequest ||
        this.pendingRequest.identity !== identity
      ) {
        this.pendingRequest = {
          identity,
          assetId,
          occasion,
          idempotencyKey: newIdempotencyKey(),
        };
      }
      this.persist();
      this.submitting = true;
      this.errorCode = null;
      this.errorMessage = null;
      try {
        const diagnosis = await createDiagnosis(
          { asset_id: assetId, occasion },
          this.pendingRequest.idempotencyKey,
          auth.accessToken,
        );
        this.accept(diagnosis);
        this.pendingRequest = null;
        this.persist();
        return diagnosis;
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
        this.persist();
        throw error;
      } finally {
        this.submitting = false;
      }
    },
    async refresh(diagnosisId?: string): Promise<Diagnosis | null> {
      const targetId = diagnosisId ?? this.activeDiagnosisId;
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
        const diagnosis = await getDiagnosis(targetId, auth.accessToken);
        this.accept(diagnosis);
        return diagnosis;
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
        return null;
      } finally {
        this.refreshing = false;
      }
    },
    accept(diagnosis: Diagnosis) {
      this.current = diagnosis;
      this.activeDiagnosisId = diagnosis.id;
      this.activeJobId = diagnosis.job_id;
      this.diagnosisIdsByJob = {
        [diagnosis.job_id]: diagnosis.id,
        ...this.diagnosisIdsByJob,
      };
      const entries = Object.entries(this.diagnosisIdsByJob).slice(
        0,
        MAX_JOB_MAPPINGS,
      );
      this.diagnosisIdsByJob = Object.fromEntries(entries);
      if (diagnosis.job_status === "COMPLETED") {
        this.recentDiagnosisId = diagnosis.id;
      }
      useJobStore().track(diagnosis.job_id);
      this.persist();
    },
    diagnosisIdForJob(jobId: string): string | null {
      return this.diagnosisIdsByJob[jobId] ?? null;
    },
    persist() {
      const persisted: PersistedDiagnosisState = {
        activeDiagnosisId: this.activeDiagnosisId,
        activeJobId: this.activeJobId,
        recentDiagnosisId: this.recentDiagnosisId,
        diagnosisIdsByJob: this.diagnosisIdsByJob,
        pendingRequest: this.pendingRequest,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
