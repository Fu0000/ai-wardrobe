import { defineStore } from "pinia";

import {
  recoverUploadPhase,
  type UploadPhase,
  uploadProgressForPhase,
} from "@/lib/asset-upload";
import {
  chooseSourceImage,
  completeUpload,
  createUploadTicket,
  removeSavedImage,
  type SelectedImage,
  uploadImageToTicket,
} from "@/services/assets";
import { useAuthStore } from "@/stores/auth";

const STORAGE_KEY = "aiw:source-draft:v1";
let abortCurrentUpload: (() => void) | null = null;

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

      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        this.errorMessage = "登录失败，请检查网络后重试。";
        this.setPhase("failed");
        return;
      }

      try {
        this.errorMessage = null;
        this.setPhase("authorizing");
        const ticket = await createUploadTicket(this.image, auth.accessToken);
        this.assetId = ticket.asset_id;
        this.setPhase("uploading");

        const directUpload = uploadImageToTicket(this.image, ticket);
        abortCurrentUpload = directUpload.abort;
        await directUpload.promise;
        abortCurrentUpload = null;

        this.setPhase("completing");
        const asset = await completeUpload(
          ticket,
          this.image.contentType,
          auth.accessToken,
        );
        this.assetId = asset.id;
        this.setPhase("ready");
      } catch (error) {
        abortCurrentUpload = null;
        if (this.phase === "cancelled") {
          return;
        }
        this.errorMessage = errorMessage(error);
        this.setPhase("failed");
      }
    },
    cancelUpload() {
      abortCurrentUpload?.();
      abortCurrentUpload = null;
      this.errorMessage = "已暂停上传，可以稍后重试。";
      this.setPhase("cancelled");
    },
    async clearDraft() {
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
