import { describe, expect, it } from "vitest";

import {
  feedbackPageUrl,
  parseWechatAttributionSource,
  shareLandingPath,
  shareLandingQuery,
} from "@/lib/navigation";

describe("navigation contracts", () => {
  it("carries diagnosis job context into the feedback page", () => {
    expect(
      feedbackPageUrl("pages/diagnosis/result", "job/id with spaces"),
    ).toBe(
      "/pages/profile/feedback?from=pages%2Fdiagnosis%2Fresult&jobId=job%2Fid%20with%20spaces",
    );
  });

  it("keeps share channel attribution in friend and timeline links", () => {
    expect(shareLandingPath("scene_-123", "WECHAT_FRIEND")).toBe(
      "/pages/share/index?scene=scene_-123&source=WECHAT_FRIEND",
    );
    expect(shareLandingQuery("scene_-123", "WECHAT_TIMELINE")).toBe(
      "scene=scene_-123&source=WECHAT_TIMELINE",
    );
  });

  it("rejects forged attribution source values", () => {
    expect(parseWechatAttributionSource("WECHAT_TIMELINE")).toBe(
      "WECHAT_TIMELINE",
    );
    expect(parseWechatAttributionSource("EMAIL")).toBeNull();
    expect(parseWechatAttributionSource(undefined)).toBeNull();
  });
});
