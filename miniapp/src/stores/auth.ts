import { defineStore } from "pinia";

import { apiRequest } from "@/services/api";

const STORAGE_KEY = "aiw:auth:v1";

interface LoginResponse {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  is_new_user: boolean;
  user: {
    id: string;
    display_name: string | null;
  };
}

interface PersistedAuth {
  accessToken: string;
  expiresAt: number;
  userId: string;
}

interface AuthState {
  status: "anonymous" | "authenticating" | "authenticated" | "failed";
  accessToken: string | null;
  expiresAt: number | null;
  userId: string | null;
}

function wechatLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    uni.login({
      provider: "weixin",
      success(result) {
        if (result.code) {
          resolve(result.code);
          return;
        }
        reject(new Error("wx.login returned no code"));
      },
      fail: reject,
    });
  });
}

export const useAuthStore = defineStore("auth", {
  state: (): AuthState => ({
    status: "anonymous",
    accessToken: null,
    expiresAt: null,
    userId: null,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as PersistedAuth | "";
      if (!persisted || persisted.expiresAt <= Date.now() + 30_000) {
        this.clear();
        return;
      }
      this.accessToken = persisted.accessToken;
      this.expiresAt = persisted.expiresAt;
      this.userId = persisted.userId;
      this.status = "authenticated";
    },
    async authenticate(forceRenew = false) {
      if (!import.meta.env.VITE_API_BASE_URL) {
        return;
      }
      if (
        !forceRenew &&
        this.status === "authenticated" &&
        this.expiresAt &&
        this.expiresAt > Date.now() + 30_000
      ) {
        return;
      }

      this.status = "authenticating";
      try {
        const code = await wechatLoginCode();
        const response = await apiRequest<LoginResponse, { code: string }>({
          path: "/api/v1/auth/wechat/login",
          method: "POST",
          body: { code },
        });
        this.accessToken = response.access_token;
        this.expiresAt = Date.now() + response.expires_in * 1_000;
        this.userId = response.user.id;
        this.status = "authenticated";
        this.persist();
      } catch {
        this.status = "failed";
      }
    },
    /**
     * 服务端拒绝当前凭据后重新登录，返回新 Token 供原请求重试。
     *
     * 必须先 clear()：本地 expiresAt 显示未过期，不清掉的话 authenticate
     * 会直接复用这份已被服务端拒绝的凭据。
     */
    async renewAfterRejection(): Promise<string | null> {
      if (this.status === "authenticating") {
        return null;
      }
      this.clear();
      await this.authenticate(true);
      return this.accessToken;
    },
    clear() {
      this.status = "anonymous";
      this.accessToken = null;
      this.expiresAt = null;
      this.userId = null;
      uni.removeStorageSync(STORAGE_KEY);
    },
    persist() {
      if (!this.accessToken || !this.expiresAt || !this.userId) {
        return;
      }
      const persisted: PersistedAuth = {
        accessToken: this.accessToken,
        expiresAt: this.expiresAt,
        userId: this.userId,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
