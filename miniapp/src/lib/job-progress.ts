export type JobStage =
  | "PENDING"
  | "QUEUED"
  | "PROCESSING"
  | "QUALITY_CHECKING"
  | "COMPLETED"
  | "FAILED_RETRYABLE"
  | "FAILED_FINAL"
  | "TIMED_OUT"
  | "CANCELLED";

interface JobStagePresentation {
  progress: number;
  label: string;
  recoverable: boolean;
}

const presentations: Record<JobStage, JobStagePresentation> = {
  PENDING: { progress: 8, label: "正在准备", recoverable: true },
  QUEUED: { progress: 18, label: "已进入分析队列", recoverable: true },
  PROCESSING: { progress: 52, label: "AI 正在理解你的穿搭", recoverable: true },
  QUALITY_CHECKING: { progress: 82, label: "正在检查结果", recoverable: true },
  COMPLETED: { progress: 100, label: "结果已准备好", recoverable: false },
  FAILED_RETRYABLE: { progress: 0, label: "这次没有成功，可以重试", recoverable: true },
  FAILED_FINAL: { progress: 0, label: "这次结果没有生成成功", recoverable: false },
  TIMED_OUT: { progress: 0, label: "任务仍在后台处理", recoverable: true },
  CANCELLED: { progress: 0, label: "任务已取消", recoverable: false },
};

export function presentJobStage(stage: JobStage): JobStagePresentation {
  return presentations[stage];
}

const optimizationPresentations: Record<JobStage, JobStagePresentation> = {
  PENDING: { progress: 8, label: "正在准备优化计划", recoverable: true },
  QUEUED: { progress: 16, label: "已进入图片生成队列", recoverable: true },
  PROCESSING: { progress: 48, label: "正在执行最小改变", recoverable: true },
  QUALITY_CHECKING: {
    progress: 86,
    label: "正在检查身份与衣物一致性",
    recoverable: true,
  },
  COMPLETED: { progress: 100, label: "Before / After 已准备好", recoverable: false },
  FAILED_RETRYABLE: {
    progress: 0,
    label: "这次没有成功，正在安全重试",
    recoverable: true,
  },
  FAILED_FINAL: { progress: 0, label: "这次优化图没有生成成功", recoverable: false },
  TIMED_OUT: { progress: 0, label: "任务仍在后台处理", recoverable: true },
  CANCELLED: { progress: 0, label: "任务已取消", recoverable: false },
};

export function presentOptimizationStage(
  stage: JobStage,
): JobStagePresentation {
  return optimizationPresentations[stage];
}

const POLL_DELAYS_MS = [1_500, 2_500, 4_000, 6_500, 10_000, 15_000] as const;

export function nextJobPollDelay(attempt: number): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  return POLL_DELAYS_MS[Math.min(safeAttempt, POLL_DELAYS_MS.length - 1)];
}
