import type { StorageProvider } from "./preferences.ts";

export const TOKEN_STORAGE_KEY = "hqlab-monitor-token";
// Non-secret change marker, not a session ID. An empty -> empty logout must
// still invalidate legacy migrations before another tab receives storage events.
export const AUTH_REVISION_KEY = "hqlab-monitor-auth-revision";
export class AuthError extends Error {}
export interface AuthSnapshot {
  readonly token: string;
  readonly version: number;
}
interface SharedAuth {
  token: string;
  revision: string;
}
export interface AuthOptions {
  local: StorageProvider;
  session: StorageProvider;
  onSharedChange: () => void;
  revision?: () => string;
}

/** Credential lifecycle, independent of the DOM, fetch and browser timers. */
export function createAuth({
  local,
  session,
  onSharedChange,
  revision = () => `${Date.now()}-${Math.random().toString(36).slice(2)}`,
}: AuthOptions) {
  function readShared(): SharedAuth | null {
    try {
      return {
        token: local().getItem(TOKEN_STORAGE_KEY) || "",
        revision: local().getItem(AUTH_REVISION_KEY) || "",
      };
    } catch {
      return null;
    }
  }
  let shared = readShared(),
    version = 0,
    legacyToken = "";
  function discardSession() {
    try {
      session().removeItem(TOKEN_STORAGE_KEY);
      session().removeItem(AUTH_REVISION_KEY);
    } catch {
      /* Disabled storage. */
    }
  }
  function readToken(): string {
    if (shared?.token) {
      discardSession();
      return shared.token;
    }
    try {
      const token = session().getItem(TOKEN_STORAGE_KEY) || "";
      if (
        shared?.revision &&
        session().getItem(AUTH_REVISION_KEY) !== shared.revision
      ) {
        discardSession();
        return "";
      }
      legacyToken = token;
      return token;
    } catch {
      return "";
    }
  }
  let token = readToken();
  function writeRevision(): string {
    const next = revision();
    try {
      local().setItem(AUTH_REVISION_KEY, next);
      return next;
    } catch {
      return shared?.revision || "";
    }
  }
  // Only called after explicit validation or one successful legacy validation.
  function save(): boolean {
    if (!token) return true;
    try {
      if (!shared) throw new Error("Persistent storage unavailable");
      local().setItem(TOKEN_STORAGE_KEY, token);
    } catch {
      try {
        session().setItem(AUTH_REVISION_KEY, shared?.revision || "");
        session().setItem(TOKEN_STORAGE_KEY, token);
      } catch {
        /* In-memory authentication still works. */
      }
      return false;
    }
    shared = { token, revision: writeRevision() };
    discardSession();
    return true;
  }
  function clear(broadcast = true) {
    token = "";
    version++;
    legacyToken = "";
    if (broadcast) {
      const revision = writeRevision();
      try {
        local().removeItem(TOKEN_STORAGE_KEY);
      } catch {
        /* Disabled storage. */
      }
      shared = { token: "", revision };
    }
    discardSession();
  }
  function sync(): boolean {
    const current = readShared();
    if (!current) return false;
    if (
      current.token === (shared?.token || "") &&
      current.revision === (shared?.revision || "")
    ) {
      shared = current;
      return false;
    }
    shared = current;
    version++;
    legacyToken = "";
    token = current.token;
    discardSession();
    onSharedChange();
    return true;
  }
  const snapshot = (): AuthSnapshot => ({ token, version });
  function check(auth: AuthSnapshot) {
    // Read actual shared storage: storage events can be delayed or obsolete.
    if (sync() || auth.token !== token || auth.version !== version)
      throw new AuthError("登录状态已变更");
  }
  function beginLogin(value: string): AuthSnapshot {
    token = value;
    version++;
    legacyToken = "";
    return snapshot();
  }
  function completeLogin(auth: AuthSnapshot): boolean {
    check(auth);
    return save();
  }
  function migrateLegacy(auth: AuthSnapshot) {
    if (!legacyToken || legacyToken !== auth.token) return;
    check(auth);
    legacyToken = ""; // One validated attempt per page, never write on polling.
    save();
  }
  return {
    get token() {
      return token;
    },
    snapshot,
    check,
    clear,
    sync,
    beginLogin,
    completeLogin,
    migrateLegacy,
  };
}
export type Auth = ReturnType<typeof createAuth>;
