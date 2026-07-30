import { runInNewContext } from "node:vm";

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiError } from "@/services/api";
import {
  type SelectedImage,
  uploadImageToTicket,
} from "@/services/assets";

const image: SelectedImage = {
  localPath: "wxfile://draft.jpg",
  contentType: "image/jpeg",
  sizeBytes: 4,
  width: 800,
  height: 1_200,
};

const ticket = {
  asset_id: "asset-1",
  upload_url: "http://localhost:8000/local-object/upload",
  method: "PUT" as const,
  headers: { "Content-Type": "image/jpeg" },
  expires_at: "2026-07-30T12:00:00Z",
};

interface ReadFileOptions {
  success: (result: { data: unknown }) => void;
}

interface RequestOptions {
  data: unknown;
  success: (response: { statusCode: number }) => void;
}

describe("asset upload binary normalization", () => {
  let readData: unknown;
  let uploadedData: unknown;

  beforeEach(() => {
    uploadedData = null;
    vi.stubGlobal("uni", {
      getFileSystemManager: () => ({
        readFile(options: ReadFileOptions) {
          options.success({ data: readData });
        },
      }),
      request(options: RequestOptions) {
        uploadedData = options.data;
        options.success({ statusCode: 204 });
        return { abort: vi.fn() };
      },
    });
  });

  it("accepts an ArrayBuffer returned from another JavaScript realm", async () => {
    readData = runInNewContext("new ArrayBuffer(4)") as ArrayBuffer;

    await uploadImageToTicket(image, ticket).promise;

    expect(Object.prototype.toString.call(uploadedData)).toBe(
      "[object ArrayBuffer]",
    );
    expect((uploadedData as ArrayBuffer).byteLength).toBe(4);
  });

  it("copies only the selected bytes from a typed-array view", async () => {
    readData = new Uint8Array([0, 1, 2, 3]).subarray(1, 3);

    await uploadImageToTicket(image, ticket).promise;

    expect(Array.from(new Uint8Array(uploadedData as ArrayBuffer))).toEqual([
      1,
      2,
    ]);
  });

  it("still rejects non-binary file data", async () => {
    readData = "not-binary";

    await expect(uploadImageToTicket(image, ticket).promise).rejects.toEqual(
      expect.objectContaining<Partial<ApiError>>({
        code: "IMAGE_READ_FAILED",
      }),
    );
  });
});
