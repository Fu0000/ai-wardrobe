interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly statusCode: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ApiRequestOptions<TBody> {
  path: string;
  method?: UniApp.RequestOptions["method"];
  body?: TBody;
  accessToken?: string;
  headers?: Record<string, string>;
}

function apiBaseUrl(): string {
  const value = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "");
  if (!value) {
    throw new ApiError(
      "API_NOT_CONFIGURED",
      "服务地址尚未配置。",
      0,
    );
  }
  return value;
}

export async function apiRequest<TResponse, TBody = never>(
  options: ApiRequestOptions<TBody>,
): Promise<TResponse> {
  return new Promise<TResponse>((resolve, reject) => {
    uni.request({
      url: `${apiBaseUrl()}${options.path}`,
      method: options.method ?? "GET",
      data: options.body as UniApp.RequestOptions["data"],
      header: {
        "Content-Type": "application/json",
        ...options.headers,
        ...(options.accessToken
          ? { Authorization: `Bearer ${options.accessToken}` }
          : {}),
      },
      success(response: UniApp.RequestSuccessCallbackResult) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data as unknown as TResponse);
          return;
        }

        const payload = response.data as unknown as ApiErrorPayload;
        reject(
          new ApiError(
            payload.error?.code ?? "REQUEST_FAILED",
            payload.error?.message ?? "服务暂时不可用，请稍后重试。",
            response.statusCode,
            payload.error?.request_id,
          ),
        );
      },
      fail(error: UniApp.GeneralCallbackResult) {
        reject(new ApiError("NETWORK_ERROR", error.errMsg, 0));
      },
    });
  });
}
