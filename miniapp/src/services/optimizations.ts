import { type JobStage } from "@/lib/job-progress";
import { apiRequest } from "@/services/api";

export interface OptimizationChange {
  priority: number;
  action: string;
  instruction: string;
  reason: string;
  preserves: string;
}

export interface Optimization {
  id: string;
  diagnosis_id: string;
  job_id: string;
  status: "PENDING" | "COMPLETED" | "REJECTED_BY_CRITIC" | "FAILED";
  job_status: JobStage;
  change_level: 1 | 2 | 3;
  changes: OptimizationChange[];
  quality_passed: boolean;
  critic_first_pass: boolean | null;
  before_image_url: string | null;
  after_image_url: string | null;
  error_code: string | null;
  user_message: string | null;
  created_at: string;
  updated_at: string;
  reused: boolean;
  quota_remaining: number | null;
}

interface CreateOptimizationBody {
  max_change_level: 1 | 2 | 3;
}

export function createOptimization(
  diagnosisId: string,
  idempotencyKey: string,
  accessToken: string,
  maxChangeLevel: 1 | 2 | 3 = 3,
): Promise<Optimization> {
  return apiRequest<Optimization, CreateOptimizationBody>({
    path: `/api/v1/style-diagnoses/${diagnosisId}/optimizations`,
    method: "POST",
    body: { max_change_level: maxChangeLevel },
    accessToken,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getOptimization(
  optimizationId: string,
  accessToken: string,
): Promise<Optimization> {
  return apiRequest<Optimization>({
    path: `/api/v1/style-optimizations/${optimizationId}`,
    accessToken,
  });
}
