// Required elements are part of the owned HTML contract. Fail clearly if it is
// broken, rather than scattering non-null assertions through every view.
export function $<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T {
  const node = root.querySelector<T>(selector);
  if (!node) throw new Error(`Missing UI element: ${selector}`);
  return node;
}
export function $$<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T[] {
  return [...root.querySelectorAll<T>(selector)];
}
export function closest<T extends Element = HTMLElement>(
  target: EventTarget | null,
  selector: string,
): T | null {
  return target instanceof Element ? target.closest<T>(selector) : null;
}
export function inputValue(event: Event): string {
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement)
    return target.value;
  throw new Error("Expected an input or select event");
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
export function createToast() {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (message: string) => {
    clearTimeout(timer);
    $("#toast").textContent = message;
    $("#toast").hidden = false;
    timer = setTimeout(() => {
      $("#toast").hidden = true;
    }, 3500);
  };
}
