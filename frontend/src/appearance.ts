import {
  APPEARANCE_KEY,
  paletteColors,
  parseAppearance,
  isTheme,
  isPalette,
} from "./appearance-preferences.ts";
import type { Appearance } from "./appearance-preferences.ts";
import { $ } from "./dom.ts";
import { escapeHTML } from "./format.ts";

// This entry is built separately as a blocking classic script, before CSS.
const media = matchMedia("(prefers-color-scheme: dark)");
const root = document.documentElement;
const defaults: Appearance = {
  theme: isTheme(root.dataset.defaultTheme)
    ? root.dataset.defaultTheme
    : "system",
  palette: isPalette(root.dataset.defaultPalette)
    ? root.dataset.defaultPalette
    : "green",
};
function read(): Appearance {
  try {
    return parseAppearance(localStorage.getItem(APPEARANCE_KEY), defaults);
  } catch {
    return { ...defaults };
  }
}
let preference = read();
function apply() {
  const dark =
    preference.theme === "dark" ||
    (preference.theme === "system" && media.matches);
  root.dataset.theme = dark ? "dark" : "light";
  root.dataset.palette = preference.palette;
  const colors = paletteColors[preference.palette];
  document
    .querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')
    .forEach((meta, index) => {
      meta.content = colors[index === 1 ? 1 : 0];
      meta.media = index === Number(dark) ? "all" : "not all";
    });
  const theme = document.querySelector<HTMLSelectElement>("#appearance-theme"),
    palette = document.querySelector<HTMLSelectElement>("#appearance-palette");
  if (theme) theme.value = preference.theme;
  if (palette) palette.value = preference.palette;
  if (document.readyState !== "loading") {
    const style = getComputedStyle(root);
    const color = (name: string, fallback: string) => {
      const value = style.getPropertyValue(name).trim();
      return /^#[0-9a-f]{3,8}$/i.test(value) ? value : fallback;
    };
    const mark = escapeHTML(root.dataset.brandMark || "C");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="17" fill="${color("--green", "#286b4f")}"/><text x="32" y="44" text-anchor="middle" font-family="sans-serif" font-weight="700" font-size="40" fill="${color("--on-accent", "#fff")}">${mark}</text></svg>`;
    const favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (favicon && favicon.dataset.custom !== "/brand-icon.png")
      favicon.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  }
}
apply();
document.addEventListener(
  "DOMContentLoaded",
  () => {
    apply();
    const controls = $(".appearance-controls"),
      toggle = $("#appearance-toggle"),
      panel = $("#appearance-panel");
    function closePanel(restoreFocus = false) {
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (restoreFocus) toggle.focus();
    }
    function openPanel(focusFirst = true) {
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      // Do not force a native mobile picker open for pointer users.
      if (focusFirst) $("#appearance-theme").focus();
      else toggle.focus();
    }
    toggle.addEventListener("click", (event) => {
      if (panel.hidden) openPanel(event.detail === 0);
      else closePanel(true);
    });
    controls.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) {
        event.preventDefault();
        event.stopPropagation();
        closePanel(true);
      } else if (event.key === "ArrowDown" && event.target === toggle) {
        event.preventDefault();
        openPanel();
      }
    });
    controls.addEventListener("focusout", (event) => {
      if (
        event.relatedTarget instanceof Node &&
        !controls.contains(event.relatedTarget)
      )
        closePanel();
    });
    document.addEventListener("click", (event) => {
      if (event.target instanceof Node && !controls.contains(event.target))
        closePanel();
    });
    function persist() {
      apply();
      try {
        localStorage.setItem(APPEARANCE_KEY, JSON.stringify(preference));
      } catch {
        window.dispatchEvent(new Event("appearance-storage-error"));
      }
    }
    $<HTMLSelectElement>("#appearance-theme").addEventListener(
      "change",
      (event) => {
        const value = (event.currentTarget as HTMLSelectElement).value;
        if (!isTheme(value)) {
          apply();
          return;
        }
        preference.theme = value;
        persist();
      },
    );
    $<HTMLSelectElement>("#appearance-palette").addEventListener(
      "change",
      (event) => {
        const value = (event.currentTarget as HTMLSelectElement).value;
        if (!isPalette(value)) {
          apply();
          return;
        }
        preference.palette = value;
        persist();
      },
    );
  },
  { once: true },
);
const followSystem = () => {
  if (preference.theme === "system") apply();
};
if (media.addEventListener) media.addEventListener("change", followSystem);
else media.addListener(followSystem);
window.addEventListener("storage", (event) => {
  if (event.key === APPEARANCE_KEY || event.key === null) {
    preference = read();
    apply();
  }
});
