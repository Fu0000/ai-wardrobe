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
  timeoutMs?: number;
  /** 内部使用：标记这是 401 之后的重试，避免无限循环。 */
  isRetry?: boolean;
}

/**
 * 默认超时。
 *
 * 微信默认 60 秒，在 6 个页面的轮询场景下慢请求会与后续轮询叠加堆积。
 * 15 秒足以覆盖正常的同步接口，异步任务本身走 Job 轮询而非长连接。
 */
const DEFAULT_TIMEOUT_MS = 15_000;

/**
 * 服务端判定凭据失效后的重新认证钩子。
 *
 * api.ts 不能直接 import auth store——auth store 依赖 apiRequest 完成登录，
 * 直接引用会形成循环依赖。改由 auth store 在初始化时注入。
 */
type Reauthenticator = () => Promise<string | null>;

let reauthenticate: Reauthenticator | null = null;

export function setReauthenticator(handler: Reauthenticator | null): void {
  reauthenticate = handler;
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

function sendRequest<TResponse, TBody>(
  options: ApiRequestOptions<TBody>,
): Promise<TResponse> {
  return new Promise<TResponse>((resolve, reject) => {
    uni.request({
      url: `${apiBaseUrl()}${options.path}`,
      method: options.method ?? "GET",
      data: options.body as UniApp.RequestOptions["data"],
      timeout: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
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

/** 401/403 意味着服务端不认这份凭据，本地过期时间已不可信。 */
function isCredentialRejected(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.statusCode === 401 || error.statusCode === 403)
  );
}

export async function apiRequest<TResponse, TBody = never>(
  options: ApiRequestOptions<TBody>,
): Promise<TResponse> {
  try {
    return await sendRequest<TResponse, TBody>(options);
  } catch (error) {
    // 只有带凭据的首次请求值得重试。authenticate() 自身的登录请求不带
    // accessToken，不会走到这里，因此不存在递归登录。
    if (
      !isCredentialRejected(error) ||
      options.isRetry ||
      !options.accessToken ||
      !reauthenticate
    ) {
      throw error;
    }

    // 本地 expiresAt 未到期但服务端已拒绝：密钥轮换、凭据撤销或时钟偏移。
    // 不重新登录的话，后续所有请求都会持续 401 直到本地时间自然过期。
    const refreshedToken = await reauthenticate();
    if (!refreshedToken) {
      throw error;
    }

    return sendRequest<TResponse, TBody>({
      ...options,
      accessToken: refreshedToken,
      isRetry: true,
    });
  }
}
