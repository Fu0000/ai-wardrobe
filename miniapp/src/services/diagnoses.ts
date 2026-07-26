import { apiRequest } from "@/services/api";
import { type JobStage } from "@/lib/job-progress";

export type Occasion =
  | "DAILY"
  | "SCHOOL"
  | "WORK"
  | "INTERVIEW"
  | "DATE"
  | "SOCIAL"
  | "TRAVEL"
  | "OTHER";

export type InputQuality =
  | "ACCEPTABLE"
  | "TOO_DARK"
  | "TOO_BLURRY"
  | "PERSON_NOT_VISIBLE"
  | "OUTFIT_OCCLUDED"
  | "MULTIPLE_PEOPLE"
  | "UNSAFE"
  | "UNKNOWN";

export type Confidence = "HIGH" | "MEDIUM" | "LOW";

export interface DiagnosisPoint {
  title: string;
  explanation: string;
  visual_evidence: string;
  confidence: Confidence;
}

export interface PrimaryIssue {
  category: string;
  title: string;
  explanation: string;
  expected_impact: string;
  confidence: Confidence;
}

export interface OptimizationStep {
  priority: number;
  action: string;
  instruction: string;
  reason: string;
  preserves: string;
}

export interface DiagnosisResult {
  input_quality: InputQuality;
  input_quality_message: string | null;
  score: number | null;
  summary: string | null;
  strengths: DiagnosisPoint[];
  issues: DiagnosisPoint[];
  primary_issue: PrimaryIssue | null;
  optimization_plan: OptimizationStep[];
  disclaimer: string;
}

export interface Diagnosis {
  id: string;
  job_id: string;
  occasion: Occasion;
  status: "PENDING" | "COMPLETED" | "FAILED";
  job_status: JobStage;
  result: DiagnosisResult | null;
  error_code: string | null;
  user_message: string | null;
  created_at: string;
  updated_at: string;
  reused: boolean;
  quota_remaining: number | null;
}

interface CreateDiagnosisBody {
  asset_id: string;
  occasion: Occasion;
}

export function createDiagnosis(
  body: CreateDiagnosisBody,
  idempotencyKey: string,
  accessToken: string,
): Promise<Diagnosis> {
  return apiRequest<Diagnosis, CreateDiagnosisBody>({
    path: "/api/v1/style-diagnoses",
    method: "POST",
    body,
    accessToken,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getDiagnosis(
  diagnosisId: string,
  accessToken: string,
): Promise<Diagnosis> {
  return apiRequest<Diagnosis>({
    path: `/api/v1/style-diagnoses/${diagnosisId}`,
    accessToken,
  });
}
