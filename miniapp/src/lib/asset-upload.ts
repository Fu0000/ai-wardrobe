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

export interface UploadRunState {
  /** 发起该次上传时的代次。 */
  generation: number;
  /** 当前最新代次；取消或重新发起都会推进它。 */
  currentGeneration: number;
  phase: UploadPhase;
}

/**
 * 判断一次上传是否已经失效，不该再写回状态。
 *
 * 每个 await 之后都要问一次。直传可以 abort，但获取 Ticket 与 Complete 走的是
 * 无法中断的普通请求——它们仍会正常返回，若不加判断就会把用户已撤回的上传
 * 复活成 ready，或让取消后重新发起的上传被上一轮覆盖。
 */
export function isUploadRunStale(state: UploadRunState): boolean {
  return state.generation !== state.currentGeneration || state.phase === "cancelled";
}
