import { inferImageContentType } from "@/lib/asset-upload";
import { ApiError, apiRequest } from "@/services/api";
import type { Deletion } from "@/services/profile";

export interface SelectedImage {
  localPath: string;
  contentType: string;
  sizeBytes: number;
  width: number;
  height: number;
}

export interface CompletedAsset {
  id: string;
  status: "READY";
  content_type: string;
  width: number;
  height: number;
}

interface UploadTicket {
  asset_id: string;
  upload_url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_at: string;
}

interface DirectUpload {
  promise: Promise<void>;
  abort: () => void;
}

function imageInfo(path: string): Promise<UniApp.GetImageInfoSuccessData> {
  return new Promise((resolve, reject) => {
    uni.getImageInfo({
      src: path,
      success: resolve,
      fail: reject,
    });
  });
}

function saveDraftFile(tempFilePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    uni.saveFile({
      tempFilePath,
      success(result) {
        resolve(result.savedFilePath);
      },
      fail: reject,
    });
  });
}

export function chooseSourceImage(): Promise<SelectedImage> {
  return new Promise((resolve, reject) => {
    uni.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      sizeType: ["compressed"],
      success: async (result) => {
        const file = result.tempFiles[0];
        if (!file) {
          reject(new ApiError("IMAGE_NOT_SELECTED", "没有选中图片。", 0));
          return;
        }

        try {
          const info = await imageInfo(file.tempFilePath);
          const contentType = info.type
            ? inferImageContentType(info.type)
            : null;
          if (!contentType) {
            throw new ApiError(
              "UNSUPPORTED_IMAGE_TYPE",
              "请选择 JPEG、PNG 或 WebP 图片。",
              0,
            );
          }
          const localPath = await saveDraftFile(file.tempFilePath);
          resolve({
            localPath,
            contentType,
            sizeBytes: file.size,
            width: info.width,
            height: info.height,
          });
        } catch (error) {
          reject(error);
        }
      },
      fail(error) {
        reject(new ApiError("IMAGE_SELECTION_CANCELLED", error.errMsg, 0));
      },
    });
  });
}

function normalizeFileData(data: unknown): ArrayBuffer | null {
  // 微信开发者工具可能从另一个 JS Realm 返回 ArrayBuffer，此时
  // `instanceof ArrayBuffer` 会错误地返回 false；部分 uni-app 版本还会
  // 包一层 Uint8Array。统一按二进制标签/视图归一化，避免误报读取失败。
  if (Object.prototype.toString.call(data) === "[object ArrayBuffer]") {
    return data as ArrayBuffer;
  }
  if (ArrayBuffer.isView(data)) {
    const view = data as ArrayBufferView;
    return view.buffer.slice(
      view.byteOffset,
      view.byteOffset + view.byteLength,
    ) as ArrayBuffer;
  }
  return null;
}

function readFileAsArrayBuffer(filePath: string): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    uni.getFileSystemManager().readFile({
      filePath,
      success(result) {
        const fileData = normalizeFileData(result.data);
        if (fileData) {
          resolve(fileData);
          return;
        }
        reject(new ApiError("IMAGE_READ_FAILED", "无法读取所选图片。", 0));
      },
      fail(error) {
        reject(new ApiError("IMAGE_READ_FAILED", error.errMsg, 0));
      },
    });
  });
}

function uploadToSignedUrl(
  ticket: UploadTicket,
  fileData: ArrayBuffer,
): DirectUpload {
  let task: UniApp.RequestTask | undefined;
  const promise = new Promise<void>((resolve, reject) => {
    task = uni.request({
      url: ticket.upload_url,
      method: "PUT",
      data: fileData,
      header: ticket.headers,
      timeout: 60_000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve();
          return;
        }
        reject(
          new ApiError(
            "OBJECT_UPLOAD_FAILED",
            "图片上传失败，请重试。",
            response.statusCode,
          ),
        );
      },
      fail(error) {
        reject(new ApiError("OBJECT_UPLOAD_FAILED", error.errMsg, 0));
      },
    });
  });

  return {
    promise,
    abort: () => task?.abort(),
  };
}

export async function createUploadTicket(
  image: SelectedImage,
  accessToken: string,
): Promise<UploadTicket> {
  return apiRequest<
    UploadTicket,
    { content_type: string; size_bytes: number }
  >({
    path: "/api/v1/assets/upload-ticket",
    method: "POST",
    body: {
      content_type: image.contentType,
      size_bytes: image.sizeBytes,
    },
    accessToken,
  });
}

export function uploadImageToTicket(
  image: SelectedImage,
  ticket: UploadTicket,
): DirectUpload {
  let currentAbort: () => void = () => {};
  const promise = readFileAsArrayBuffer(image.localPath).then((fileData) => {
    const upload = uploadToSignedUrl(ticket, fileData);
    currentAbort = upload.abort;
    return upload.promise;
  });
  return {
    promise,
    abort: () => currentAbort(),
  };
}

export async function completeUpload(
  ticket: UploadTicket,
  contentType: string,
  accessToken: string,
): Promise<CompletedAsset> {
  return apiRequest<CompletedAsset, { content_type: string }>({
    path: `/api/v1/assets/${ticket.asset_id}/complete`,
    method: "POST",
    body: { content_type: contentType },
    accessToken,
  });
}

export function requestPhotoDeletion(
  assetId: string,
  idempotencyKey: string,
  accessToken: string,
): Promise<Deletion> {
  return apiRequest<Deletion>({
    path: `/api/v1/me/photos/${assetId}`,
    method: "DELETE",
    accessToken,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getPhotoDeletionStatus(
  assetId: string,
  accessToken: string,
): Promise<Deletion> {
  return apiRequest<Deletion>({
    path: `/api/v1/me/photos/${assetId}/deletion-status`,
    accessToken,
  });
}

export function removeSavedImage(filePath: string): Promise<void> {
  return new Promise((resolve) => {
    uni.removeSavedFile({
      filePath,
      complete: () => resolve(),
    });
  });
}
