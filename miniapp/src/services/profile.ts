import { apiRequest } from "@/services/api";

export interface Profile {
  id: string;
  display_name: string | null;
  consent_version: string | null;
  has_ai_processing_consent: boolean;
}

interface UpdateProfileBody {
  display_name: string | null;
  consent_version?: string;
  has_ai_processing_consent: boolean;
}

export function getProfile(accessToken: string): Promise<Profile> {
  return apiRequest<Profile>({
    path: "/api/v1/me",
    accessToken,
  });
}

export function updateProfile(
  body: UpdateProfileBody,
  accessToken: string,
): Promise<Profile> {
  return apiRequest<Profile, UpdateProfileBody>({
    path: "/api/v1/me/profile",
    method: "POST",
    body,
    accessToken,
  });
}

export type DeletionStatus =
  | "PENDING"
  | "PROCESSING"
  | "COMPLETED"
  | "FAILED_RETRYABLE"
  | "FAILED_FINAL";

export interface Deletion {
  id: string;
  job_id: string | null;
  status: DeletionStatus;
  completed_steps: string[];
  user_message: string;
  can_retry: boolean;
  requested_at: string;
  updated_at: string;
  reused: boolean;
}

export function requestAccountDeletion(
  idempotencyKey: string,
  accessToken: string,
): Promise<Deletion> {
  return apiRequest<Deletion>({
    path: "/api/v1/me/deletion-request",
    method: "POST",
    accessToken,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getAccountDeletionStatus(
  accessToken: string,
): Promise<Deletion> {
  return apiRequest<Deletion>({
    path: "/api/v1/me/deletion-status",
    accessToken,
  });
}
