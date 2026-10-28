export interface StorageAccess {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
// Access the browser storage property lazily: its getter itself can throw.
export type StorageProvider = () => StorageAccess;
export const PIN_STORAGE_KEY = "hqlab-monitor-pinned-hosts";
export function readPinnedHosts(storage: StorageProvider): string[] {
  try {
    const saved: unknown = JSON.parse(
      storage().getItem(PIN_STORAGE_KEY) || "[]",
    );
    return Array.isArray(saved)
      ? [
          ...new Set(
            saved.filter(
              (name): name is string =>
                typeof name === "string" &&
                name.length > 0 &&
                name.length <= 255,
            ),
          ),
        ]
      : [];
  } catch {
    return [];
  }
}
export function savePinnedHosts(
  storage: StorageProvider,
  names: string[],
): boolean {
  try {
    storage().setItem(PIN_STORAGE_KEY, JSON.stringify(names));
    return true;
  } catch {
    return false;
  }
}
