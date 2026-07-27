import { type JobStage } from "@/lib/job-progress";
import { apiRequest } from "@/services/api";

export type ShareStatus =
  | "PENDING"
  | "ACTIVE"
  | "FAILED"
  | "EXPIRED"
  | "REVOKED";
export type VoteChoice = "BEFORE" | "AFTER";
export type AttributionSource =
  | "WECHAT_FRIEND"
  | "WECHAT_TIMELINE"
  | "PREVIEW";

export interface PublicChange {
  priority: number;
  instruction: string;
  reason: string;
}

export interface Share {
  id: string;
  scene_code: string;
  job_id: string | null;
  status: ShareStatus;
  job_status: JobStage | null;
  card_url: string | null;
  change_level: 1 | 2 | 3;
  changes: PublicChange[];
  score: number | null;
  ai_edited: boolean;
  votes: {
    before: number;
    after: number;
  };
  viewer_choice: VoteChoice | null;
  expires_at: string | null;
  error_code: string | null;
  user_message: string | null;
  reused: boolean;
}

interface CreateShareBody {
  optimization_id: string;
  display_score: boolean;
  attribution_source: AttributionSource;
}

export interface VoteResponse {
  scene_code: string;
  choice: VoteChoice;
  votes: {
    before: number;
    after: number;
  };
  reused: boolean;
}

export function createShare(
  body: CreateShareBody,
  idempotencyKey: string,
  accessToken: string,
): Promise<Share> {
  return apiRequest<Share, CreateShareBody>({
    path: "/api/v1/shares",
    method: "POST",
    body,
    accessToken,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getShare(
  sceneCode: string,
  accessToken: string,
  attributionSource?: AttributionSource,
): Promise<Share> {
  const query = attributionSource
    ? `?attribution_source=${attributionSource}`
    : "";
  return apiRequest<Share>({
    path: `/api/v1/shares/${sceneCode}${query}`,
    accessToken,
  });
}

export function submitVote(
  sceneCode: string,
  choice: VoteChoice,
  accessToken: string,
): Promise<VoteResponse> {
  return apiRequest<
    VoteResponse,
    {
      scene_code: string;
      choice: VoteChoice;
    }
  >({
    path: "/api/v1/votes",
    method: "POST",
    body: {
      scene_code: sceneCode,
      choice,
    },
    accessToken,
  });
}

export function recordShareContinue(
  sceneCode: string,
  accessToken: string,
): Promise<{ attributed: boolean }> {
  return apiRequest<{ attributed: boolean }>({
    path: `/api/v1/shares/${sceneCode}/continue`,
    method: "POST",
    accessToken,
  });
}

export function recordShareInvocation(
  sceneCode: string,
  attributionSource: Exclude<AttributionSource, "PREVIEW">,
  accessToken: string,
): Promise<{ recorded: boolean }> {
  return apiRequest<
    { recorded: boolean },
    { attribution_source: Exclude<AttributionSource, "PREVIEW"> }
  >({
    path: `/api/v1/shares/${sceneCode}/invocations`,
    method: "POST",
    body: { attribution_source: attributionSource },
    accessToken,
  });
}
