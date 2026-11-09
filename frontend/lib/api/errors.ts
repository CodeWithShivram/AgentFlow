// The API error envelope (backend/app/core/errors.py) and the client-side error type.

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id: string | null;
  details: Record<string, unknown>;
}

export interface FieldError {
  loc: (string | number)[];
  message: string;
  type: string;
}

const REVISION_CONFLICT_CODES = new Set([
  "WORKFLOW_REVISION_CONFLICT",
  "TASK_REVISION_CONFLICT",
  "AGENT_REVISION_CONFLICT",
]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    body: ApiErrorBody,
    retryAfterSeconds: number | null = null,
  ) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id;
    this.details = body.details;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  get isRevisionConflict(): boolean {
    return REVISION_CONFLICT_CODES.has(this.code);
  }

  get fieldErrors(): FieldError[] {
    const errors = this.details.errors;
    return Array.isArray(errors) ? errors.filter(isFieldError) : [];
  }
}

/** The request never produced an HTTP response (offline, DNS, CORS, aborted). */
export class NetworkError extends Error {
  constructor() {
    super("The AgentFlow API could not be reached. Check your connection and try again.");
    this.name = "NetworkError";
  }
}

function isFieldError(value: unknown): value is FieldError {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return Array.isArray(v.loc) && typeof v.message === "string";
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.code === "string" && typeof v.message === "string";
}

const FALLBACK_MESSAGES: Record<number, string> = {
  400: "The request could not be processed.",
  401: "Your session has ended. Sign in again.",
  403: "You do not have permission to do that.",
  404: "This item does not exist or you do not have access to it.",
  409: "This item changed while you were working. Reload and try again.",
  422: "Some values are not valid.",
  429: "Too many requests. Wait a moment and try again.",
};

/** Builds an ApiError from any error response, never exposing non-JSON bodies. */
export function toApiError(status: number, payload: unknown, retryAfter: string | null): ApiError {
  const envelope =
    typeof payload === "object" && payload !== null
      ? (payload as Record<string, unknown>).error
      : undefined;
  const retryAfterSeconds = retryAfter !== null && /^\d+$/.test(retryAfter) ? Number(retryAfter) : null;
  if (isErrorBody(envelope)) {
    return new ApiError(
      status,
      {
        code: envelope.code,
        message: envelope.message,
        request_id: typeof envelope.request_id === "string" ? envelope.request_id : null,
        details:
          typeof envelope.details === "object" && envelope.details !== null
            ? (envelope.details as Record<string, unknown>)
            : {},
      },
      retryAfterSeconds,
    );
  }
  return new ApiError(
    status,
    {
      code: status >= 500 ? "SERVER_ERROR" : "HTTP_ERROR",
      message:
        FALLBACK_MESSAGES[status] ??
        (status >= 500 ? "The server had a problem. Try again shortly." : "The request failed."),
      request_id: null,
      details: {},
    },
    retryAfterSeconds,
  );
}

/** A short message that is safe and useful to show to the user for any thrown value. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return error.retryAfterSeconds
        ? `Too many requests. Try again in ${error.retryAfterSeconds} seconds.`
        : FALLBACK_MESSAGES[429];
    }
    if (error.status === 422 && error.fieldErrors.length > 0) {
      return error.fieldErrors
        .map((e) => `${fieldLabel(e.loc)}: ${e.message}`)
        .slice(0, 3)
        .join(" ");
    }
    if (error.status >= 500) return "The server had a problem. Try again shortly.";
    return error.message;
  }
  if (error instanceof NetworkError) return error.message;
  return "Something went wrong. Try again.";
}

function fieldLabel(loc: (string | number)[]): string {
  const parts = loc.filter((p) => p !== "body");
  return parts.length > 0 ? parts.join(".") : "request";
}
