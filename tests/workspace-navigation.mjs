// Browser-only navigation regressions, called by browser-smoke.mjs after login.
import assert from "node:assert/strict";
import {setTimeout as sleep} from "node:timers/promises";

export async function checkWorkspaceNavigation({cdp, evaluate, click, setInput, waitFor, screenshot, token}) {
  const sections = ["overview", "history", "servers", "processes"];
  const current = "document.querySelector('.sidebar [aria-current=location]')?.dataset.nav";
  assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('.sidebar [data-nav]'), el=>el.dataset.nav)"), sections, "menu follows page order");
  assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('#overview,#history,#servers,#processes'), el=>el.id)"), sections);
  assert.equal(await evaluate(current), "overview", "initial location is accessible without scrolling");
  assert.equal(await evaluate("Array.from(document.querySelectorAll('.sidebar [data-nav]')).every(el=>el.hash==='#'+el.dataset.nav && el.title && el.getAttribute('aria-label'))"), true, "icon-only navigation retains names and tooltips");

  async function atAnchor(id, label) {
    await waitFor(`(() => {
      const section=document.getElementById(${JSON.stringify(id)});
      const offset=parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop);
      const destination=Math.max(0,Math.min(document.documentElement.scrollHeight-innerHeight,scrollY+section.getBoundingClientRect().top-offset));
      return location.hash===${JSON.stringify(`#${id}`)} && ${current}===${JSON.stringify(id)} && Math.abs(scrollY-destination)<=2;
    })()`, label).catch(async error => {
      const geometry = await evaluate(`({hash:location.hash,width:innerWidth,y:scrollY,max:document.documentElement.scrollHeight-innerHeight,offset:getComputedStyle(document.documentElement).scrollPaddingTop,top:document.getElementById(${JSON.stringify(id)}).getBoundingClientRect().top,active:${current}})`);
      await screenshot("workspace-navigation-failure.png");
      throw new Error(`${label}: ${JSON.stringify(geometry)}`, {cause:error});
    });
    assert.equal(await evaluate("document.querySelectorAll('.sidebar .active').length"), 1, `${label}: one active item`);
    assert.equal(await evaluate("document.querySelector('.sidebar .active').getAttribute('aria-current')"), "location", `${label}: visual and accessible states agree`);
    assert.equal(await evaluate(`(() => {
      const heading=document.querySelector('#${id} h1,#${id} h2').getBoundingClientRect();
      const sidebar=document.querySelector('.sidebar'), style=getComputedStyle(sidebar);
      const obstruction=style.position==='sticky'?sidebar.getBoundingClientRect().bottom:0;
      return heading.top>=obstruction && heading.bottom<=innerHeight;
    })()`), true, `${label}: heading visible, not hidden behind mobile navigation`);
  }
  async function navigate(id) {
    await click(`[data-nav='${id}']`);
    await atAnchor(id, `navigate to ${id}`);
  }
  async function viewport(width, height = 900) {
    await cdp("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor:1, mobile:width < 500});
    await sleep(180);
  }

  // Exercise native fragment scrolling as well as the narrow sticky header.
  for (const width of [1440, 768, 390, 320]) {
    await viewport(width);
    for (const id of sections) await navigate(id);
    await navigate("history");
    if (width === 1440 || width === 390) await screenshot(`workspace-navigation-${width}.png`);
    await navigate("servers"); await navigate("processes");
    await evaluate("history.back()"); await atAnchor("servers", `${width}px: Back`);
    await evaluate("history.forward()"); await atAnchor("processes", `${width}px: Forward`);
  }
  for (const width of [1440, 390]) {
    await viewport(width);
    for (let attempt = 0; attempt < 3; attempt++) {
      await navigate("overview");
      for (const id of ["servers", "history", "processes"]) {
        await click(`[data-nav='${id}']`); await sleep(60);
      }
      await atAnchor("processes", `${width}px: rapid navigation keeps the last destination`);
    }
  }
  await viewport(1440, 1100);
  await click("#choose-pinned"); await atAnchor("servers", "choose pinned server link");
  await click(".sidebar .brand"); await atAnchor("overview", "brand link");
  await evaluate("document.querySelector('[data-nav=history]').focus({preventScroll:true})");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown", key:"Enter", code:"Enter", windowsVirtualKeyCode:13, text:"\r", unmodifiedText:"\r"});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp", key:"Enter", code:"Enter", windowsVirtualKeyCode:13});
  await atAnchor("history", "keyboard Enter");
  assert.equal(await evaluate("document.activeElement.id"), "history-title", "keyboard fragment navigation focuses the destination heading");

  // Scrolling must update the location without filling the browser history.
  const historyLength = await evaluate("history.length");
  await evaluate("window.scrollTo({top:0,behavior:'instant'})");
  await waitFor(`${current}==='overview'`, "manual scroll to top");
  await evaluate("window.scrollTo({top:scrollY+document.querySelector('#history').getBoundingClientRect().top+100,behavior:'instant'})");
  await waitFor(`${current}==='history'`, "manual scroll to history");
  assert.equal(await evaluate("history.length"), historyLength, "scrollspy does not add history entries");

  // With browser scroll anchoring disabled, pinning moves headings without a
  // scroll event. Layout observation must still update the highlighted item.
  await evaluate("document.documentElement.style.overflowAnchor='none'");
  const beforePin = await evaluate("scrollY");
  await click("#node-grid [data-pin-node]");
  await waitFor(`${current}==='overview'`, "pin expansion updates location without scrolling");
  assert.equal(await evaluate("scrollY"), beforePin);
  await click("#node-grid [data-pin-node]");
  await waitFor(`${current}==='history'`, "pin removal updates location without scrolling");
  await evaluate("document.documentElement.style.removeProperty('overflow-anchor')");

  // Filtering can make several final headings share the bottom-clamped target.
  await setInput("#server-search", "no-such-server");
  await setInput("#process-search", "no-such-process");
  await navigate("servers");
  assert.equal(await evaluate("Math.abs(scrollY+innerHeight-document.documentElement.scrollHeight)<=2"), true, "short filtered sections hit the document bottom");
  await navigate("processes");
  await navigate("servers");
  await navigate("servers"); // Same hash and no scroll event still selects it.
  await cdp("Input.dispatchMouseEvent", {type:"mouseWheel", x:1300, y:800, deltaX:0, deltaY:500});
  await waitFor(`${current}==='processes'`, "manual scrolling at the bottom releases the clicked section");
  await screenshot("workspace-navigation-short-page.png");
  await setInput("#server-search", ""); await setInput("#process-search", "");
  await waitFor("document.querySelectorAll('#node-grid .node-card').length===8", "restore unfiltered data");

  // A reload with a fragment must wait for authenticated content, not retain
  // the position of the much shorter loading skeleton.
  await viewport(390);
  await navigate("servers");
  await evaluate("localStorage.removeItem('hqlab-monitor-token');sessionStorage.removeItem('hqlab-monitor-token')");
  const oldOrigin = await evaluate("performance.timeOrigin");
  await cdp("Page.reload"); await sleep(300);
  await waitFor(`performance.timeOrigin!==${oldOrigin} && document.querySelector('#auth-dialog')?.open`, "fragment opens behind authentication");
  await setInput("#access-token", token);
  await evaluate("document.querySelector('#auth-form').requestSubmit()");
  await waitFor("!document.querySelector('#auth-dialog').open && !document.querySelector('#refresh-button').disabled && !!document.querySelector('.chart-primary')", "fragment data loaded after login");
  await atAnchor("servers", "fragment restored after login and first render");
  await click("#auto-refresh");
  await navigate("processes");
  const authenticatedOrigin = await evaluate("performance.timeOrigin");
  await cdp("Page.reload"); await sleep(300);
  await waitFor(`performance.timeOrigin!==${authenticatedOrigin} && !document.querySelector('#refresh-button').disabled && !!document.querySelector('.chart-primary')`, "authenticated fragment reload");
  await atAnchor("processes", "fragment restored after authenticated reload");
  await click("#auto-refresh");
  await evaluate("window.scrollTo({top:0,behavior:'instant'})");
  await waitFor(`${current}==='overview'`, "manual scroll away from fragment");
  await click("#refresh-button");
  await waitFor("!document.querySelector('#refresh-button').disabled", "refresh after fragment navigation");
  assert.equal(await evaluate("scrollY"), 0, "later refreshes do not snap back to the URL fragment");
  assert.equal(await evaluate(current), "overview");

  // Reduced motion uses the same anchor offsets and selection rules.
  await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme", value:"light"}, {name:"prefers-reduced-motion", value:"reduce"}]});
  for (const id of sections) await navigate(id);
  await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme", value:"light"}]});
  await viewport(1440, 1100);
  await navigate("overview");
  await evaluate("history.replaceState(null,'',location.pathname+location.search);window.scrollTo({top:0,behavior:'instant'});document.activeElement.blur();");
}
