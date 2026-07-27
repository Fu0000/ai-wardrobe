import { type AttributionSource } from "@/services/shares";

export type WechatAttributionSource = Extract<
  AttributionSource,
  "WECHAT_FRIEND" | "WECHAT_TIMELINE"
>;

export function feedbackPageUrl(
  originPage: string,
  relatedJobId?: string | null,
): string {
  const query = [`from=${encodeURIComponent(originPage)}`];
  if (relatedJobId) {
    query.push(`jobId=${encodeURIComponent(relatedJobId)}`);
  }
  return `/pages/profile/feedback?${query.join("&")}`;
}

export function shareLandingPath(
  sceneCode: string,
  source: WechatAttributionSource,
): string {
  return `/pages/share/index?${shareLandingQuery(sceneCode, source)}`;
}

export function shareLandingQuery(
  sceneCode: string,
  source: WechatAttributionSource,
): string {
  return `scene=${encodeURIComponent(sceneCode)}&source=${source}`;
}

export function parseWechatAttributionSource(
  value: unknown,
): WechatAttributionSource | null {
  return value === "WECHAT_FRIEND" || value === "WECHAT_TIMELINE"
    ? value
    : null;
}
