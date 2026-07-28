import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/services/api";
import {
  getShare,
  recordShareInvocation,
} from "@/services/shares";

vi.mock("@/services/api", () => ({
  apiRequest: vi.fn(),
}));

const apiRequestMock = vi.mocked(apiRequest);

describe("share API contract", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({} as never);
  });

  it("passes a validated timeline source when opening a shared scene", async () => {
    await getShare("scene_code_123456", "access-token", "WECHAT_TIMELINE");

    expect(apiRequestMock).toHaveBeenCalledWith({
      path: "/api/v1/shares/scene_code_123456?attribution_source=WECHAT_TIMELINE",
      accessToken: "access-token",
    });
  });

  it("records the channel that invoked the WeChat share menu", async () => {
    await recordShareInvocation(
      "scene_code_123456",
      "WECHAT_FRIEND",
      "access-token",
    );

    expect(apiRequestMock).toHaveBeenCalledWith({
      path: "/api/v1/shares/scene_code_123456/invocations",
      method: "POST",
      body: { attribution_source: "WECHAT_FRIEND" },
      accessToken: "access-token",
    });
  });
});
