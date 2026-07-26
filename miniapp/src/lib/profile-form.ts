/**
 * 个人资料页的加载与保存状态。
 *
 * 授权开关的初值是 false，而服务端真值只能靠加载拿到。若加载失败后仍允许保存，
 * 表单会把「尚未读到」当成「用户选择关闭」提交上去，静默撤销 AI 授权——
 * 由于 AI_CONSENT_REQUIRED 门禁全部 AI 能力，用户会在无提示的情况下失去核心功能。
 *
 * 因此保存的前置条件不是「没有报错」，而是「确实读到过服务端真值」。
 */

export type ProfileLoadState = "loading" | "ready" | "failed";

export interface ProfileFormSnapshot {
  loadState: ProfileLoadState;
  saving: boolean;
}

/** 表单是否可编辑。未读到真值时不得渲染表单，避免用户对着默认值操作。 */
export function canEditProfile(snapshot: ProfileFormSnapshot): boolean {
  return snapshot.loadState === "ready";
}

/** 是否允许提交。加载未成功或正在保存时一律拒绝。 */
export function canSubmitProfile(snapshot: ProfileFormSnapshot): boolean {
  return snapshot.loadState === "ready" && !snapshot.saving;
}

/** 加载失败时应展示重试入口而非表单。 */
export function shouldOfferReload(snapshot: ProfileFormSnapshot): boolean {
  return snapshot.loadState === "failed";
}

export interface ConsentPayloadInput {
  displayName: string;
  hasConsent: boolean;
  consentVersion: string;
}

export interface ConsentPayload {
  display_name: string | null;
  has_ai_processing_consent: boolean;
  consent_version?: string;
}

/**
 * 构造资料更新载荷。
 *
 * 撤回授权时不带 consent_version——版本号描述的是「同意了哪一版条款」，
 * 未同意时它没有意义。
 */
export function buildProfilePayload(input: ConsentPayloadInput): ConsentPayload {
  const displayName = input.displayName.trim();
  return {
    display_name: displayName || null,
    has_ai_processing_consent: input.hasConsent,
    ...(input.hasConsent ? { consent_version: input.consentVersion } : {}),
  };
}
