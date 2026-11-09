import { ApiClient } from "./client";
import { createEndpoints } from "./endpoints";

// Public, build-time configuration only. Never put secrets in NEXT_PUBLIC_* variables.
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const apiClient = new ApiClient({ baseUrl: API_BASE_URL });
export const api = createEndpoints(apiClient);

export { ApiError, NetworkError, describeError } from "./errors";
export type * from "./types";
