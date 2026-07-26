import { apiRequest } from "@/services/api";

export type FeedbackCategory =
  | "AI_QUALITY"
  | "BUG"
  | "EXPERIENCE"
  | "PRIVACY"
  | "OTHER";

export interface CreateFeedbackBody {
  category: FeedbackCategory;
  rating: number | null;
  message: string;
  related_job_id?: string;
  page?: string;
  app_version?: string;
  platform?: string;
  system_version?: string;
  wechat_version?: string;
  network_type?: string;
  trace_id?: string;
}

export interface Feedback {
  id: string;
  category: FeedbackCategory;
  status: "NEW" | "TRIAGED" | "RESOLVED";
  rating: number | null;
  message: string;
  related_job_id: string | null;
  created_at: string;
  reused: boolean;
}

export function createFeedback(
  body: CreateFeedbackBody,
  idempotencyKey: string,
  accessToken: string,
): Promise<Feedback> {
  return apiRequest<Feedback, CreateFeedbackBody>({
    path: "/api/v1/feedback",
    method: "POST",
    body,
    accessToken,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}
