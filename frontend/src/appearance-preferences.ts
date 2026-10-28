export const APPEARANCE_KEY = "hqlab-monitor-appearance";
export const themes = ["system", "light", "dark"] as const;
export const paletteColors = {
  green: ["#f5f7f5", "#101914"],
  blue: ["#f4f7fb", "#111827"],
  purple: ["#f7f5fb", "#191520"],
  amber: ["#faf7f0", "#1e1911"],
  teal: ["#f1f8f7", "#111d1c"],
  rose: ["#fbf5f7", "#21151c"],
} as const;
export type Theme = (typeof themes)[number];
export type Palette = keyof typeof paletteColors;
export interface Appearance {
  theme: Theme;
  palette: Palette;
}
export const isTheme = (value: unknown): value is Theme =>
  themes.some((theme) => theme === value);
export const isPalette = (value: unknown): value is Palette =>
  typeof value === "string" && Object.hasOwn(paletteColors, value);
export function parseAppearance(
  raw: string | null,
  defaults: Appearance,
): Appearance {
  try {
    const value: unknown = JSON.parse(raw || "null");
    if (!value || typeof value !== "object") return { ...defaults };
    return {
      theme:
        "theme" in value && isTheme(value.theme) ? value.theme : defaults.theme,
      palette:
        "palette" in value && isPalette(value.palette)
          ? value.palette
          : defaults.palette,
    };
  } catch {
    return { ...defaults };
  }
}
