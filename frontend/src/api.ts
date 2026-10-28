import { AuthError } from "./auth.ts";
import type { Auth } from "./auth.ts";
import type { Overview, History } from "./types.ts";

export interface ApiOptions {
  auth: Auth;
  fetch: typeof fetch;
  onUnauthorized: () => void;
  timeout?: () => AbortSignal;
}
export function createApi({
  auth,
  fetch: fetcher,
  onUnauthorized,
  timeout = () => AbortSignal.timeout(20000),
}: ApiOptions) {
  async function request(
    path: string,
    snapshot = auth.snapshot(),
  ): Promise<Response> {
    auth.check(snapshot);
    const response = await fetcher(path, {
      headers: snapshot.token
        ? { Authorization: `Bearer ${snapshot.token}` }
        : {},
      signal: timeout(),
      cache: "no-store",
    });
    auth.check(snapshot);
    if (response.status === 401) {
      auth.clear(!!snapshot.token);
      onUnauthorized();
      throw new AuthError("需要访问令牌");
    }
    if (!response.ok) {
      let message = `请求失败 (${response.status})`;
      try {
        const body: unknown = await response.json();
        if (
          body &&
          typeof body === "object" &&
          "error" in body &&
          typeof body.error === "string"
        )
          message = body.error || message;
      } catch {
        /* Non-JSON gateway errors. */
      }
      auth.check(snapshot);
      throw new Error(message);
    }
    return response;
  }
  async function json<T>(path: string): Promise<T> {
    const snapshot = auth.snapshot();
    const data: T = await (await request(path, snapshot)).json();
    auth.check(snapshot); // The body may resolve after another login or logout.
    return data;
  }
  async function exportCsv(params: URLSearchParams): Promise<Blob> {
    const snapshot = auth.snapshot();
    const blob = await (
      await request(`/api/export.csv?${params}`, snapshot)
    ).blob();
    auth.check(snapshot);
    return blob;
  }
  return {
    overview: () => json<Overview>("/api/overview"),
    history: (params: URLSearchParams) =>
      json<History>(`/api/history?${params}`),
    exportCsv,
  };
}
export type Api = ReturnType<typeof createApi>;
