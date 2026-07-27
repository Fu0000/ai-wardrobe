import { defineStore } from "pinia";

import { ApiError } from "@/services/api";
import {
  createShare,
  getShare,
  recordShareContinue,
  recordShareInvocation,
  submitVote,
  type AttributionSource,
  type Share,
  type VoteChoice,
} from "@/services/shares";
import { useAuthStore } from "@/stores/auth";
import { useJobStore } from "@/stores/jobs";

const STORAGE_KEY = "aiw:shares:v1";
const MAX_JOB_MAPPINGS = 20;

interface PendingShareRequest {
  optimizationId: string;
  displayScore: boolean;
  attributionSource: AttributionSource;
  idempotencyKey: string;
}

interface PersistedShareState {
  activeSceneCode: string | null;
  activeJobId: string | null;
  recentSceneCode: string | null;
  sceneCodesByJob: Record<string, string>;
  pendingRequest: PendingShareRequest | null;
}

interface ShareState extends PersistedShareState {
  current: Share | null;
  submitting: boolean;
  refreshing: boolean;
  voting: boolean;
  errorCode: string | null;
  errorMessage: string | null;
}

function newIdempotencyKey(): string {
  return `share-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") {
    return "网络断开了，请检查连接后继续。分享请求不会重复创建。";
  }
  if (error instanceof ApiError && error.statusCode === 410) {
    return "这份分享已经失效。";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "分享服务暂时不可用，请稍后重试。";
}

export const useShareStore = defineStore("shares", {
  state: (): ShareState => ({
    activeSceneCode: null,
    activeJobId: null,
    recentSceneCode: null,
    sceneCodesByJob: {},
    pendingRequest: null,
    current: null,
    submitting: false,
    refreshing: false,
    voting: false,
    errorCode: null,
    errorMessage: null,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as
        | PersistedShareState
        | "";
      if (!persisted) {
        return;
      }
      this.activeSceneCode = persisted.activeSceneCode;
      this.activeJobId = persisted.activeJobId;
      this.recentSceneCode = persisted.recentSceneCode;
      this.sceneCodesByJob = persisted.sceneCodesByJob ?? {};
      this.pendingRequest = persisted.pendingRequest;
    },
    async create(
      optimizationId: string,
      displayScore: boolean,
      attributionSource: AttributionSource = "WECHAT_FRIEND",
      forceNew = false,
    ): Promise<Share> {
      if (this.submitting) {
        throw new Error("分享卡片正在创建，请稍候。");
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        throw new Error("登录失败，请检查网络后重试。");
      }
      if (
        forceNew ||
        !this.pendingRequest ||
        this.pendingRequest.optimizationId !== optimizationId ||
        this.pendingRequest.displayScore !== displayScore ||
        this.pendingRequest.attributionSource !== attributionSource
      ) {
        this.pendingRequest = {
          optimizationId,
          displayScore,
          attributionSource,
          idempotencyKey: newIdempotencyKey(),
        };
      }
      this.persist();
      this.submitting = true;
      this.errorCode = null;
      this.errorMessage = null;
      try {
        const share = await createShare(
          {
            optimization_id: optimizationId,
            display_score: displayScore,
            attribution_source: attributionSource,
          },
          this.pendingRequest.idempotencyKey,
          auth.accessToken,
        );
        this.accept(share);
        this.pendingRequest = null;
        this.persist();
        return share;
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
        this.persist();
        throw error;
      } finally {
        this.submitting = false;
      }
    },
    async refresh(
      sceneCode?: string,
      attributionSource?: AttributionSource,
    ): Promise<Share | null> {
      const target = sceneCode ?? this.activeSceneCode;
      if (!target || this.refreshing) {
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
        const share = await getShare(
          target,
          auth.accessToken,
          attributionSource,
        );
        this.accept(share);
        return share;
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
        return null;
      } finally {
        this.refreshing = false;
      }
    },
    async vote(choice: VoteChoice): Promise<void> {
      if (!this.current || this.current.status !== "ACTIVE" || this.voting) {
        return;
      }
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        this.errorMessage = "登录失败，请检查网络后重试。";
        return;
      }
      this.voting = true;
      this.errorCode = null;
      this.errorMessage = null;
      try {
        const result = await submitVote(
          this.current.scene_code,
          choice,
          auth.accessToken,
        );
        this.current = {
          ...this.current,
          viewer_choice: result.choice,
          votes: result.votes,
        };
      } catch (error) {
        this.errorCode = error instanceof ApiError ? error.code : "UNKNOWN";
        this.errorMessage = safeErrorMessage(error);
      } finally {
        this.voting = false;
      }
    },
    async recordContinue(sceneCode: string): Promise<void> {
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        return;
      }
      try {
        await recordShareContinue(sceneCode, auth.accessToken);
      } catch {
        // Attribution is non-blocking; never trap a user on the shared landing page.
      }
    },
    async recordInvocation(
      sceneCode: string,
      attributionSource: Exclude<AttributionSource, "PREVIEW">,
    ): Promise<void> {
      const auth = useAuthStore();
      await auth.authenticate();
      if (!auth.accessToken) {
        return;
      }
      try {
        await recordShareInvocation(
          sceneCode,
          attributionSource,
          auth.accessToken,
        );
      } catch {
        // 分享菜单必须立即返回；埋点失败不能阻止微信完成分享。
      }
    },
    accept(share: Share) {
      this.current = share;
      this.activeSceneCode = share.scene_code;
      this.activeJobId = share.job_id;
      if (share.job_id) {
        this.sceneCodesByJob = {
          [share.job_id]: share.scene_code,
          ...this.sceneCodesByJob,
        };
        this.sceneCodesByJob = Object.fromEntries(
          Object.entries(this.sceneCodesByJob).slice(0, MAX_JOB_MAPPINGS),
        );
        useJobStore().track(share.job_id);
      }
      if (share.status === "ACTIVE") {
        this.recentSceneCode = share.scene_code;
      }
      this.persist();
    },
    sceneCodeForJob(jobId: string): string | null {
      return this.sceneCodesByJob[jobId] ?? null;
    },
    persist() {
      const persisted: PersistedShareState = {
        activeSceneCode: this.activeSceneCode,
        activeJobId: this.activeJobId,
        recentSceneCode: this.recentSceneCode,
        sceneCodesByJob: this.sceneCodesByJob,
        pendingRequest: this.pendingRequest,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
