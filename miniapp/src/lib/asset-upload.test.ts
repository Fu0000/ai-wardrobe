import { describe, expect, it } from "vitest";

import {
  inferImageContentType,
  recoverUploadPhase,
  uploadProgressForPhase,
} from "./asset-upload";

describe("asset upload state", () => {
  it("maps supported image formats to server content types", () => {
    expect(inferImageContentType("JPG")).toBe("image/jpeg");
    expect(inferImageContentType("png")).toBe("image/png");
    expect(inferImageContentType("webp")).toBe("image/webp");
    expect(inferImageContentType("gif")).toBeNull();
  });

  it("recovers interrupted transient phases as retryable failures", () => {
    expect(recoverUploadPhase("uploading")).toBe("failed");
    expect(recoverUploadPhase("completing")).toBe("failed");
    expect(recoverUploadPhase("ready")).toBe("ready");
  });

  it("uses phase progress without pretending to know byte progress", () => {
    expect(uploadProgressForPhase("authorizing")).toBe(12);
    expect(uploadProgressForPhase("uploading")).toBe(35);
    expect(uploadProgressForPhase("completing")).toBe(90);
    expect(uploadProgressForPhase("ready")).toBe(100);
  });
});
