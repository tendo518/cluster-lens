import { $, $$, closest } from "./dom.ts";
// Native fragments, Back/Forward, bottom-clamped destinations and first
// authenticated-render restoration; scrollspy never writes browser history.
export function createWorkspaceNavigation() {
  const workspaceLinks = $$<HTMLAnchorElement>(".sidebar [data-nav]").map(
    (link) => ({ link, section: $("#" + link.dataset.nav) }),
  );
  let navigationQueued = false,
    initialAnchorPending = true;
  let navigationIntent: {
    entry: (typeof workspaceLinks)[number];
    arrived: boolean;
  } | null = null;
  function rememberWorkspaceAnchor(hash: string) {
    const entry = workspaceLinks.find(({ link }) => link.hash === hash);
    navigationIntent = entry ? { entry, arrived: false } : null;
    queueWorkspaceNavigation();
  }
  function updateWorkspaceNavigation() {
    const offset =
      parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) ||
      0;
    const maxScroll = Math.max(
      0,
      document.documentElement.scrollHeight - innerHeight,
    );
    let active = workspaceLinks[0];
    for (const entry of workspaceLinks)
      if (entry.section.getBoundingClientRect().top <= offset + 2)
        active = entry;
    // A short final table cannot necessarily reach the top of the viewport.
    if (scrollY > 0 && scrollY >= maxScroll - 2)
      active = workspaceLinks[workspaceLinks.length - 1];
    if (navigationIntent) {
      const destination = Math.max(
        0,
        Math.min(
          maxScroll,
          scrollY +
            navigationIntent.entry.section.getBoundingClientRect().top -
            offset,
        ),
      );
      if (Math.abs(scrollY - destination) <= 2) {
        // Several short sections can share the same clamped scroll destination.
        active = navigationIntent.entry;
        if (!navigationIntent.arrived)
          active.section
            .querySelector<HTMLElement>("h1,h2")
            ?.focus({ preventScroll: true });
        navigationIntent.arrived = true;
      } else if (navigationIntent.arrived) navigationIntent = null;
    }
    for (const entry of workspaceLinks) {
      entry.link.classList.toggle("active", entry === active);
      if (entry === active) entry.link.setAttribute("aria-current", "location");
      else entry.link.removeAttribute("aria-current");
    }
  }
  function queueWorkspaceNavigation() {
    if (navigationQueued) return;
    navigationQueued = true;
    requestAnimationFrame(() => {
      navigationQueued = false;
      updateWorkspaceNavigation();
    });
  }
  function restoreWorkspaceAnchor(ready: boolean) {
    if (!initialAnchorPending || !ready) return;
    initialAnchorPending = false;
    // The first authenticated render expands the skeleton, pins and history.
    // Restore the fragment once, never on subsequent automatic refreshes.
    requestAnimationFrame(() => {
      rememberWorkspaceAnchor(location.hash);
      navigationIntent?.entry.section.scrollIntoView({
        block: "start",
        behavior: "instant",
      });
    });
  }
  function releaseWorkspaceAnchor() {
    navigationIntent = null;
    initialAnchorPending = false;
    queueWorkspaceNavigation();
  }
  document.addEventListener("click", (event) => {
    const link = closest<HTMLAnchorElement>(event.target, 'a[href^="#"]');
    if (
      !link ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    rememberWorkspaceAnchor(link.hash);
  });
  window.addEventListener("hashchange", () =>
    rememberWorkspaceAnchor(location.hash),
  );
  window.addEventListener("scroll", queueWorkspaceNavigation, {
    passive: true,
  });
  window.addEventListener("resize", queueWorkspaceNavigation, {
    passive: true,
  });
  window.addEventListener("wheel", releaseWorkspaceAnchor, { passive: true });
  window.addEventListener("touchmove", releaseWorkspaceAnchor, {
    passive: true,
  });
  document.addEventListener("keydown", (event) => {
    if (
      [
        "ArrowUp",
        "ArrowDown",
        "PageUp",
        "PageDown",
        "Home",
        "End",
        " ",
      ].includes(event.key) &&
      !closest(
        event.target,
        "input,select,textarea,button,[contenteditable],[role=dialog],dialog",
      )
    )
      releaseWorkspaceAnchor();
  });
  const workspaceResizeObserver = new ResizeObserver(queueWorkspaceNavigation);
  workspaceLinks.forEach(({ section }) =>
    workspaceResizeObserver.observe(section),
  );
  queueWorkspaceNavigation();
  return { restoreWorkspaceAnchor };
}
