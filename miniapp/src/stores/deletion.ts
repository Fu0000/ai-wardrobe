import { defineStore } from "pinia";

import { ApiError } from "@/services/api";
import {
  getAccountDeletionStatus,
  requestAccountDeletion,
  type Deletion,
} from "@/services/profile";
import { useAssetStore } from "@/stores/assets";
import { useAuthStore } from "@/stores/auth";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useJobStore } from "@/stores/jobs";
import { useOptimizationStore } from "@/stores/optimizations";
import { usePhotoDeletionStore } from "@/stores/photo-deletion";
import { useShareStore } from "@/stores/shares";

const STORAGE_KEY = "aiw:deletion:v1";
const COMPLETED_KEY = "aiw:deletion-completed:v1";

interface PersistedDeletionState {
  active: boolean;
  requestedUserId: string | null;
  idempotencyKey: string | null;
}

interface DeletionState extends PersistedDeletionState {
  current: Deletion | null;
  submitting: boolean;
  refreshing: boolean;
  errorMessage: string | null;
  inferredCompleted: boolean;
}

function newIdempotencyKey(): string {
  return `account-deletion-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 14)}`;
}

function safeError(error: unknown): string {
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") {
    return "网络断开了，删除请求已保留，请稍后刷新。";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "删除状态暂时无法读取，请稍后重试。";
}

export const useDeletionStore = defineStore("deletion", {
  state: (): DeletionState => ({
    active: false,
    requestedUserId: null,
    idempotencyKey: null,
    current: null,
    submitting: false,
    refreshing: false,
    errorMessage: null,
    inferredCompleted: false,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as
        | PersistedDeletionState
        | "";
      if (!persisted) {
        return;
      }
      this.active = persisted.active;
      this.requestedUserId = persisted.requestedUserId;
      this.idempotencyKey = persisted.idempotencyKey;
    },
    async reconcileIdentity(currentUserId: string | null) {
      if (
        this.active &&
        this.requestedUserId &&
        currentUserId &&
        currentUserId !== this.requestedUserId
      ) {
        this.inferredCompleted = true;
        this.active = false;
        await this.clearPrivateLocalState(true);
      }
    },
    async request(forceNew = false): Promise<Deletion | null> {
      if (this.submitting) {
        return this.current;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken || !auth.userId) {
        this.errorMessage = "登录失败，请检查网络后重试。";
        return null;
      }
      if (forceNew || !this.idempotencyKey) {
        this.idempotencyKey = newIdempotencyKey();
      }
      this.active = true;
      this.requestedUserId = auth.userId;
      this.persist();
      this.submitting = true;
      this.errorMessage = null;
      try {
        const deletion = await requestAccountDeletion(
          this.idempotencyKey,
          auth.accessToken,
        );
        await this.accept(deletion);
        return deletion;
      } catch (error) {
        this.errorMessage = safeError(error);
        this.persist();
        return null;
      } finally {
        this.submitting = false;
      }
    },
    async refresh(): Promise<Deletion | null> {
      if (!this.active || this.refreshing) {
        return this.current;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      await this.reconcileIdentity(auth.userId);
      if (this.inferredCompleted || !auth.accessToken) {
        return null;
      }
      this.refreshing = true;
      this.errorMessage = null;
      try {
        const deletion = await getAccountDeletionStatus(auth.accessToken);
        await this.accept(deletion);
        return deletion;
      } catch (error) {
        this.errorMessage = safeError(error);
        return null;
      } finally {
        this.refreshing = false;
      }
    },
    async accept(deletion: Deletion) {
      this.current = deletion;
      this.active = deletion.status !== "COMPLETED";
      if (deletion.status === "COMPLETED") {
        await this.clearPrivateLocalState();
      } else {
        this.persist();
      }
    },
    async clearPrivateLocalState(preserveCurrentAuth = false) {
      const assets = useAssetStore();
      if (!assets.image) {
        assets.hydrate();
      }
      await assets.clearDraft();

      const keys = uni.getStorageInfoSync().keys;
      for (const key of keys) {
        if (
          key.startsWith("aiw:") &&
          !(preserveCurrentAuth && key === "aiw:auth:v1")
        ) {
          uni.removeStorageSync(key);
        }
      }
      // 清 storage 不清 Pinia。这些 store 在 onLaunch 时 hydrate 后常驻内存，
      // 不重置的话注销当次会话内仍能读到诊断结论、优化前后图与分享记录。
      useDiagnosisStore().$reset();
      useOptimizationStore().$reset();
      useShareStore().$reset();
      usePhotoDeletionStore().$reset();
      useJobStore().$reset();

      uni.setStorageSync(COMPLETED_KEY, {
        completedAt: Date.now(),
      });
      if (!preserveCurrentAuth) {
        useAuthStore().clear();
      }
      this.idempotencyKey = null;
      this.requestedUserId = null;
    },
    persist() {
      const persisted: PersistedDeletionState = {
        active: this.active,
        requestedUserId: this.requestedUserId,
        idempotencyKey: this.idempotencyKey,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
