import { defineStore } from "pinia";

import {
  isUploadRunStale,
  recoverUploadPhase,
  type UploadPhase,
  uploadProgressForPhase,
} from "@/lib/asset-upload";
import {
  chooseSourceImage,
  completeUpload,
  createUploadTicket,
  removeSavedImage,
  requestPhotoDeletion,
  type SelectedImage,
  uploadImageToTicket,
} from "@/services/assets";
import { ApiError } from "@/services/api";
import {
  track,
  trackOnce,
  uploadProgressBucket,
  uploadSizeBucket,
} from "@/services/telemetry";
import { useAuthStore } from "@/stores/auth";

const STORAGE_KEY = "aiw:source-draft:v1";
let abortCurrentUpload: (() => void) | null = null;
// 每次取消或重新发起都会推进代次，旧的上传流程据此判断自己已失效。
let uploadGeneration = 0;

function newIdempotencyKey(): string {
  return `cancelled-upload-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 14)}`;
}

interface PersistedDraft {
  image: SelectedImage;
  assetId: string | null;
  phase: UploadPhase;
}

interface AssetState {
  image: SelectedImage | null;
  assetId: string | null;
  phase: UploadPhase;
  progress: number;
  errorMessage: string | null;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "图片上传失败，请稍后重试。";
}

function interruptionReason(
  error: unknown,
): "network" | "timeout" | "unknown" {
  if (error instanceof Error && error.message.toLowerCase().includes("timeout")) {
    return "timeout";
  }
  if (
    error instanceof ApiError &&
    (error.statusCode === 0 || error.code === "NETWORK_ERROR")
  ) {
    return "network";
  }
  return "unknown";
}

export const useAssetStore = defineStore("assets", {
  state: (): AssetState => ({
    image: null,
    assetId: null,
    phase: "idle",
    progress: 0,
    errorMessage: null,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as PersistedDraft | "";
      if (!persisted || !persisted.image) {
        return;
      }
      this.image = persisted.image;
      this.assetId = persisted.assetId;
      this.phase = recoverUploadPhase(persisted.phase);
      this.progress = uploadProgressForPhase(this.phase);
      if (this.phase === "failed") {
        this.errorMessage = "上次上传被中断，可以继续重试。";
      }
    },
    setPhase(phase: UploadPhase) {
      this.phase = phase;
      this.progress = uploadProgressForPhase(phase);
      this.persist();
    },
    async selectAndUpload() {
      const previousPath = this.image?.localPath;
      try {
        const image = await chooseSourceImage();
        if (previousPath && previousPath !== image.localPath) {
          await removeSavedImage(previousPath);
        }
        this.image = image;
        this.assetId = null;
        this.errorMessage = null;
        this.setPhase("selected");
        await this.upload();
      } catch (error) {
        if (error instanceof Error && error.message.includes("cancel")) {
          return;
        }
        this.errorMessage = errorMessage(error);
        this.setPhase("failed");
      }
    },
    async upload() {
      if (!this.image) {
        return;
      }

      const generation = ++uploadGeneration;
      // 立即离开 cancelled，否则本轮自身会被判定为已失效——用户取消后就再也
      // 无法重试。代次守卫负责让上一轮失效，phase 只表达当前这一轮的进度。
      this.errorMessage = null;
      this.setPhase("selected");
      const isStale = () =>
        isUploadRunStale({
          generation,
          currentGeneration: uploadGeneration,
          phase: this.phase,
        });

      const auth = useAuthStore();
      await auth.authenticate();
      if (isStale()) {
        return;
      }
      if (!auth.accessToken) {
        this.errorMessage = "登录失败，请检查网络后重试。";
        this.setPhase("failed");
        return;
      }

      try {
        this.errorMessage = null;
        this.setPhase("authorizing");
        const ticket = await createUploadTicket(this.image, auth.accessToken);
        // Ticket 请求无法中断，返回后必须确认这次上传仍然有效，
        // 否则会把已取消的流程推进到 uploading。
        if (isStale()) {
          await this.discardCancelledAsset(ticket.asset_id, auth.accessToken);
          return;
        }
        this.assetId = ticket.asset_id;
        this.setPhase("uploading");
        trackOnce(
          `asset.upload.started:${ticket.asset_id}`,
          "asset.upload.started",
          {
            asset_id: ticket.asset_id,
            size_bucket: uploadSizeBucket(this.image.sizeBytes),
          },
        );

        const directUpload = uploadImageToTicket(this.image, ticket);
        abortCurrentUpload = directUpload.abort;
        await directUpload.promise;
        abortCurrentUpload = null;
        if (isStale()) {
          await this.discardCancelledAsset(ticket.asset_id, auth.accessToken);
          return;
        }

        this.setPhase("completing");
        const asset = await completeUpload(
          ticket,
          this.image.contentType,
          auth.accessToken,
        );
        // Complete 同样不可中断。用户在此期间取消后请求仍会成功返回，
        // 若无此判断就会把撤回的上传复活为 ready 并进入诊断流程。
        if (isStale()) {
          await this.discardCancelledAsset(asset.id, auth.accessToken);
          return;
        }
        this.assetId = asset.id;
        this.setPhase("ready");
      } catch (error) {
        abortCurrentUpload = null;
        if (isStale()) {
          return;
        }
        if (this.assetId) {
          track("asset.upload.interrupted", {
            asset_id: this.assetId,
            reason: interruptionReason(error),
            progress_bucket: uploadProgressBucket(this.progress),
          });
        }
        this.errorMessage = errorMessage(error);
        this.setPhase("failed");
      }
    },
    /**
     * 清理用户取消后仍被服务端创建出来的资产。
     *
     * 失败只记录不上抛：此时用户已经看到「已暂停上传」，再抛错只会制造
     * 与其认知不符的报错。残留资产由服务端的孤儿清理兜底。
     */
    async discardCancelledAsset(assetId: string, accessToken: string) {
      try {
        await requestPhotoDeletion(assetId, newIdempotencyKey(), accessToken);
      } catch {
        // 静默失败，理由见上。
      }
    },
    cancelUpload() {
      const interruptedAssetId = this.assetId;
      const interruptedProgress = this.progress;
      uploadGeneration += 1;
      abortCurrentUpload?.();
      abortCurrentUpload = null;
      this.errorMessage = "已暂停上传，可以稍后重试。";
      this.setPhase("cancelled");
      if (interruptedAssetId) {
        track("asset.upload.interrupted", {
          asset_id: interruptedAssetId,
          reason: "cancelled",
          progress_bucket: uploadProgressBucket(interruptedProgress),
        });
      }
    },
    async clearDraft() {
      uploadGeneration += 1;
      abortCurrentUpload?.();
      abortCurrentUpload = null;
      if (this.image?.localPath) {
        await removeSavedImage(this.image.localPath);
      }
      this.image = null;
      this.assetId = null;
      this.phase = "idle";
      this.progress = 0;
      this.errorMessage = null;
      uni.removeStorageSync(STORAGE_KEY);
    },
    persist() {
      if (!this.image) {
        return;
      }
      const persisted: PersistedDraft = {
        image: this.image,
        assetId: this.assetId,
        phase: this.phase,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
