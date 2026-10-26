"use strict";

// Run before stylesheets so saved appearance is applied before the first paint.
(() => {
  const key = "hqlab-monitor-appearance";
  const themes = ["system", "light", "dark"];
  const paletteColors = {
    green:["#f5f7f5", "#101914"], blue:["#f4f7fb", "#111827"],
    purple:["#f7f5fb", "#191520"], amber:["#faf7f0", "#1e1911"],
    teal:["#f1f8f7", "#111d1c"], rose:["#fbf5f7", "#21151c"],
  };
  const palettes = Object.keys(paletteColors);
  const media = matchMedia("(prefers-color-scheme: dark)");
  const root = document.documentElement;
  const defaults = {
    theme:themes.includes(root.dataset.defaultTheme) ? root.dataset.defaultTheme : "system",
    palette:palettes.includes(root.dataset.defaultPalette) ? root.dataset.defaultPalette : "green",
  };
  function read() {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return {theme:themes.includes(value?.theme) ? value.theme : defaults.theme, palette:palettes.includes(value?.palette) ? value.palette : defaults.palette};
    } catch {return {...defaults};}
  }
  let preference = read();
  function apply() {
    const dark = preference.theme === "dark" || preference.theme === "system" && media.matches;
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.palette = preference.palette;
    const colors = paletteColors[preference.palette];
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta, index) => {
      meta.content = colors[index]; meta.media = index === Number(dark) ? "all" : "not all";
    });
    const theme = document.querySelector("#appearance-theme"), palette = document.querySelector("#appearance-palette");
    if (theme) theme.value = preference.theme;
    if (palette) palette.value = preference.palette;
    if (document.readyState !== "loading") {
      const style = getComputedStyle(root);
      const color = (name, fallback) => {
        const value = style.getPropertyValue(name).trim();
        return /^#[0-9a-f]{3,8}$/i.test(value) ? value : fallback;
      };
      const mark = (root.dataset.brandMark || "C").replace(/[<>&"']/g, char => ({"<":"&lt;", ">":"&gt;", "&":"&amp;", '"':"&quot;", "'":"&#39;"})[char]);
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="17" fill="${color("--green", "#286b4f")}"/><text x="32" y="44" text-anchor="middle" font-family="sans-serif" font-weight="700" font-size="40" fill="${color("--on-accent", "#fff")}">${mark}</text></svg>`;
      const favicon = document.querySelector('link[rel="icon"]');
      if (favicon && favicon.dataset.custom !== "/brand-icon.png") favicon.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    }
  }
  apply();
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    const controls = document.querySelector(".appearance-controls");
    const toggle = document.getElementById("appearance-toggle"), panel = document.getElementById("appearance-panel");
    function closePanel(restoreFocus = false) {
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (restoreFocus) toggle.focus();
    }
    function openPanel(focusFirst = true) {
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      // Pointer users should see the panel, not an immediately opened mobile picker.
      if (focusFirst) document.getElementById("appearance-theme").focus();
      else toggle.focus();
    }
    toggle.addEventListener("click", event => {if (panel.hidden) openPanel(event.detail === 0); else closePanel(true);});
    controls.addEventListener("keydown", event => {
      if (event.key === "Escape" && !panel.hidden) {
        event.preventDefault(); event.stopPropagation(); closePanel(true);
      } else if (event.key === "ArrowDown" && event.target === toggle) {
        event.preventDefault(); openPanel();
      }
    });
    controls.addEventListener("focusout", event => {
      if (event.relatedTarget && !controls.contains(event.relatedTarget)) closePanel();
    });
    document.addEventListener("click", event => {if (!controls.contains(event.target)) closePanel();});
    for (const [id, field, allowed] of [["appearance-theme", "theme", themes], ["appearance-palette", "palette", palettes]]) {
      document.getElementById(id).addEventListener("change", event => {
        if (!allowed.includes(event.target.value)) {apply(); return;}
        preference[field] = event.target.value;
        apply();
        try {localStorage.setItem(key, JSON.stringify(preference));}
        catch {window.dispatchEvent(new Event("appearance-storage-error"));}
      });
    }
  }, {once:true});
  const followSystem = () => {if (preference.theme === "system") apply();};
  if (media.addEventListener) media.addEventListener("change", followSystem);
  else media.addListener(followSystem);
  window.addEventListener("storage", event => {
    if (event.key === key || event.key === null) {preference = read(); apply();}
  });
})();
