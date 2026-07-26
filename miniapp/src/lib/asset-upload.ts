export type UploadPhase =
  | "idle"
  | "selected"
  | "authorizing"
  | "uploading"
  | "completing"
  | "ready"
  | "failed"
  | "cancelled";

const CONTENT_TYPE_BY_IMAGE_FORMAT: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
};

export function inferImageContentType(imageFormat: string): string | null {
  return CONTENT_TYPE_BY_IMAGE_FORMAT[imageFormat.toLowerCase()] ?? null;
}

export function recoverUploadPhase(phase: UploadPhase): UploadPhase {
  if (
    phase === "authorizing" ||
    phase === "uploading" ||
    phase === "completing"
  ) {
    return "failed";
  }
  return phase;
}

export function uploadProgressForPhase(phase: UploadPhase): number {
  const progress: Record<UploadPhase, number> = {
    idle: 0,
    selected: 5,
    authorizing: 12,
    uploading: 35,
    completing: 90,
    ready: 100,
    failed: 0,
    cancelled: 0,
  };
  return progress[phase];
}
