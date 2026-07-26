import { defineStore } from "pinia";

import { ApiError } from "@/services/api";
import {
  getPhotoDeletionStatus,
  requestPhotoDeletion,
} from "@/services/assets";
import type { Deletion } from "@/services/profile";
import { useAssetStore } from "@/stores/assets";
import { useAuthStore } from "@/stores/auth";

const STORAGE_KEY = "aiw:photo-deletion:v1";

interface PersistedPhotoDeletion {
  active: boolean;
  assetId: string | null;
  idempotencyKey: string | null;
  current: Deletion | null;
}

interface PhotoDeletionState extends PersistedPhotoDeletion {
  submitting: boolean;
  refreshing: boolean;
  errorMessage: string | null;
}

function newIdempotencyKey(): string {
  return `photo-deletion-${Date.now()}-${Math.random()
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
  return "照片删除状态暂时无法读取，请稍后重试。";
}

export const usePhotoDeletionStore = defineStore("photoDeletion", {
  state: (): PhotoDeletionState => ({
    active: false,
    assetId: null,
    idempotencyKey: null,
    current: null,
    submitting: false,
    refreshing: false,
    errorMessage: null,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as
        | PersistedPhotoDeletion
        | "";
      if (!persisted) {
        return;
      }
      this.active = persisted.active;
      this.assetId = persisted.assetId;
      this.idempotencyKey = persisted.idempotencyKey;
      this.current = persisted.current;
    },
    async request(forceNew = false): Promise<Deletion | null> {
      if (this.submitting) {
        return this.current;
      }
      const assets = useAssetStore();
      const targetId = this.assetId ?? assets.assetId;
      if (!targetId) {
        this.errorMessage = "当前没有可删除的照片。";
        return null;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        this.errorMessage = "登录失败，请检查网络后重试。";
        return null;
      }
      if (forceNew || !this.idempotencyKey || this.assetId !== targetId) {
        this.idempotencyKey = newIdempotencyKey();
      }
      this.assetId = targetId;
      this.active = true;
      this.persist();
      this.submitting = true;
      this.errorMessage = null;
      try {
        const deletion = await requestPhotoDeletion(
          targetId,
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
      if (!this.assetId || this.refreshing) {
        return this.current;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        this.errorMessage = "登录失败，请检查网络后重试。";
        return null;
      }
      this.refreshing = true;
      this.errorMessage = null;
      try {
        const deletion = await getPhotoDeletionStatus(
          this.assetId,
          auth.accessToken,
        );
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
        const assets = useAssetStore();
        if (assets.assetId === this.assetId) {
          await assets.clearDraft();
        }
      }
      this.persist();
    },
    clear() {
      this.active = false;
      this.assetId = null;
      this.idempotencyKey = null;
      this.current = null;
      this.errorMessage = null;
      uni.removeStorageSync(STORAGE_KEY);
    },
    persist() {
      const persisted: PersistedPhotoDeletion = {
        active: this.active,
        assetId: this.assetId,
        idempotencyKey: this.idempotencyKey,
        current: this.current,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
