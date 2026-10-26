// Application-owned branding regression; isolated demo processes and fresh origins only.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {writeFileSync} from "node:fs";
import {join} from "node:path";

export async function checkBranding({root, artifacts, python, token, cdp, evaluate, waitFor, waitOutput, setInput, screenshot}) {
  const uiFile = join(artifacts, "ui.json"), iconFile = join(artifacts, "logo.png");
  // Valid 1x1 RGBA PNG, no network or image-tool dependencies.
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMwSJjwHwAEBAIg5PKlggAAAABJRU5ErkJggg==", "base64");
  writeFileSync(iconFile, png);
  const ui = {title:'Research <Team> · Monitor', name:'Research computing operations center', subtitle:'OPERATIONS', mark:'R', avatar:'RT', workspace:'Research cluster', workspace_subtitle:'Independent site', heading:'Resource monitoring', footer:'Research <Team>', theme:'dark', palette:'purple'};
  for (const customIcon of [false, true]) {
    writeFileSync(uiFile, JSON.stringify({...ui, ...(customIcon ? {icon_file:"logo.png"} : {})}));
    const child = spawn(python, ["-B", "-S", "-m", "monitor", "--demo", "--port", "0", "--db", join(artifacts, `brand-${customIcon}.sqlite3`), "--ui", uiFile], {cwd:root, env:{...process.env, HQLAB_MONITOR_TOKEN:token}, stdio:["ignore", "pipe", "pipe"]});
    let firstPaint;
    try {
      const base = await waitOutput(child, /(http:\/\/127\.0\.0\.1:\d+)/, "branded demo");
      firstPaint = await cdp("Page.addScriptToEvaluateOnNewDocument", {source:"requestAnimationFrame(function inspect(){if(!document.querySelector('#appearance-theme')){requestAnimationFrame(inspect);return;}window.firstBrand={title:document.title,theme:document.documentElement.dataset.theme,palette:document.documentElement.dataset.palette};});"});
      await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme", value:"light"}]});
      await cdp("Page.navigate", {url:base});
      await waitFor("document.querySelector('#auth-dialog')?.open && !!window.firstBrand", "configured brand login");
      assert.deepEqual(await evaluate("window.firstBrand"), {title:ui.title, theme:"dark", palette:"purple"}, "site defaults and title are present before first paint");
      assert.equal(await evaluate("document.querySelector('.brand-name').textContent"), ui.name);
      assert.equal(await evaluate("document.querySelector('.brand').getAttribute('aria-label')"), `${ui.name} 首页`);
      assert.equal(await evaluate("document.querySelector('.workspace strong').textContent"), ui.workspace);
      assert.equal(await evaluate("document.querySelector('.avatar').textContent"), ui.avatar);
      assert.equal(await evaluate("document.querySelector('.page-footer').textContent.includes('Research <Team>')"), true);
      assert.equal(await evaluate("!!window.injected"), false);
      if (customIcon) {
        await waitFor("Array.from(document.querySelectorAll('.brand-image')).length===2 && Array.from(document.querySelectorAll('.brand-image')).every(img=>img.complete && img.naturalWidth===1)", "configured PNG logos load");
        assert.equal(await evaluate("new URL(document.querySelector('link[rel=icon]').href).pathname"), "/brand-icon.png");
      } else {
        assert.match(await evaluate("decodeURIComponent(document.querySelector('link[rel=icon]').href)"), />R<\/text>/);
      }
      await setInput("#access-token", token);
      await evaluate("document.querySelector('#auth-form').requestSubmit()");
      await waitFor("!document.querySelector('#auth-dialog').open && document.querySelectorAll('#node-grid .node-card').length===8", "configured brand authenticated");
      for (const width of [1440, 390]) {
        await cdp("Emulation.setDeviceMetricsOverride", {width, height:1050, deviceScaleFactor:1, mobile:width===390});
        assert.equal(await evaluate("document.documentElement.scrollWidth<=innerWidth"), true, `long configurable brand does not overflow at ${width}px`);
        await screenshot(`brand-${customIcon}-${width}.png`);
      }
      await setInput("#appearance-theme", "light", "change");
      await setInput("#appearance-palette", "blue", "change");
      const oldOrigin = await evaluate("performance.timeOrigin");
      await cdp("Page.reload");
      await waitFor(`performance.timeOrigin!==${oldOrigin} && !!window.firstBrand && !!document.querySelector('#appearance-theme')`, "personal preference reload");
      assert.deepEqual(await evaluate("window.firstBrand"), {title:ui.title, theme:"light", palette:"blue"}, "browser choice overrides site defaults before first paint");
      await evaluate("localStorage.setItem('hqlab-monitor-appearance','broken');window.dispatchEvent(new StorageEvent('storage',{key:'hqlab-monitor-appearance'}));");
      assert.deepEqual(await evaluate("({theme:document.documentElement.dataset.theme,palette:document.documentElement.dataset.palette})"), {theme:"dark", palette:"purple"}, "corrupt preferences fall back to site settings");
      if (customIcon) assert.equal(await evaluate("new URL(document.querySelector('link[rel=icon]').href).pathname"), "/brand-icon.png", "appearance never replaces a configured icon");
    } finally {
      if (firstPaint) await cdp("Page.removeScriptToEvaluateOnNewDocument", {identifier:firstPaint.identifier});
      child.kill("SIGTERM");
    }
  }
}
