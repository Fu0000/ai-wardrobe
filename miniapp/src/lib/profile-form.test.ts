import { describe, expect, it } from "vitest";

import {
  buildProfilePayload,
  canEditProfile,
  canSubmitProfile,
  shouldOfferReload,
  type ProfileFormSnapshot,
} from "@/lib/profile-form";

const snapshot = (
  overrides: Partial<ProfileFormSnapshot> = {},
): ProfileFormSnapshot => ({
  loadState: "ready",
  saving: false,
  ...overrides,
});

describe("profile form guards", () => {
  it("blocks submission until the server value has been read", () => {
    // 核心回归：加载失败时 hasConsent 仍是初值 false，
    // 一旦放行保存就会把服务端真实的授权状态覆盖为关闭。
    expect(canSubmitProfile(snapshot({ loadState: "failed" }))).toBe(false);
    expect(canSubmitProfile(snapshot({ loadState: "loading" }))).toBe(false);
  });

  it("allows submission only once loading succeeded", () => {
    expect(canSubmitProfile(snapshot())).toBe(true);
  });

  it("blocks concurrent submissions", () => {
    expect(canSubmitProfile(snapshot({ saving: true }))).toBe(false);
  });

  it("hides the form unless the server value has been read", () => {
    expect(canEditProfile(snapshot({ loadState: "failed" }))).toBe(false);
    expect(canEditProfile(snapshot({ loadState: "loading" }))).toBe(false);
    expect(canEditProfile(snapshot())).toBe(true);
  });

  it("keeps the form editable while saving", () => {
    // 保存中只禁用提交按钮，不应把整个表单收起来。
    expect(canEditProfile(snapshot({ saving: true }))).toBe(true);
  });

  it("offers a retry entry only after a failed load", () => {
    expect(shouldOfferReload(snapshot({ loadState: "failed" }))).toBe(true);
    expect(shouldOfferReload(snapshot({ loadState: "loading" }))).toBe(false);
    expect(shouldOfferReload(snapshot())).toBe(false);
  });

  it("never lets the form be both editable and offering reload", () => {
    // 两者同时为真意味着用户既能改又被提示重载，状态自相矛盾。
    for (const loadState of ["loading", "ready", "failed"] as const) {
      const current = snapshot({ loadState });
      expect(canEditProfile(current) && shouldOfferReload(current)).toBe(false);
    }
  });
});

describe("profile payload", () => {
  it("carries the consent version when granting", () => {
    expect(
      buildProfilePayload({
        displayName: "阿磊",
        hasConsent: true,
        consentVersion: "privacy-v1",
      }),
    ).toEqual({
      display_name: "阿磊",
      has_ai_processing_consent: true,
      consent_version: "privacy-v1",
    });
  });

  it("omits the consent version when revoking", () => {
    // 版本号描述「同意了哪一版条款」，未同意时它没有意义。
    const payload = buildProfilePayload({
      displayName: "阿磊",
      hasConsent: false,
      consentVersion: "privacy-v1",
    });

    expect(payload.has_ai_processing_consent).toBe(false);
    expect("consent_version" in payload).toBe(false);
  });

  it("normalises a blank display name to null", () => {
    expect(
      buildProfilePayload({
        displayName: "   ",
        hasConsent: true,
        consentVersion: "privacy-v1",
      }).display_name,
    ).toBeNull();
  });

  it("trims surrounding whitespace", () => {
    expect(
      buildProfilePayload({
        displayName: "  阿磊  ",
        hasConsent: true,
        consentVersion: "privacy-v1",
      }).display_name,
    ).toBe("阿磊");
  });
});
