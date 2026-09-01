import { ApiError } from "./api";

/**
 * Single shared error formatter for the whole product surface.
 *
 * Replaces 14 near-identical copies that had already drifted: some prefixed the
 * diagnostic code, some did not, and the generic fallback text differed between
 * "请求未完成" and "操作未完成".
 */
export function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    return caught.code ? `${caught.code}: ${caught.message}` : caught.message;
  }
  // Transport, timeout and malformed-response failures are converted into
  // ApiError inside request(), so anything else reaching here is an unexpected
  // client-side fault. Its raw message is not user-actionable and may expose
  // internals, so it stays generic.
  return "请求未完成";
}
