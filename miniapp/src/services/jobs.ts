import { type JobStage } from "@/lib/job-progress";
import { apiRequest } from "@/services/api";

export interface Job {
  id: string;
  task_type:
    | "STYLE_DIAGNOSIS"
    | "STYLE_OPTIMIZATION"
    | "SHARE_ASSET"
    | "DELETION";
  status: JobStage;
  progress: number;
  retry_count: number;
  error_code: string | null;
  user_message: string | null;
  result_reference_type: string | null;
  result_reference_id: string | null;
  created_at: string;
  updated_at: string;
}

export function getJob(jobId: string, accessToken: string): Promise<Job> {
  return apiRequest<Job>({
    path: `/api/v1/jobs/${jobId}`,
    accessToken,
  });
}
