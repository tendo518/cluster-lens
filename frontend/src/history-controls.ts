import { $, $$, closest, inputValue, errorMessage } from "./dom.ts";
import { dateInput } from "./format.ts";
import { nowSeconds } from "./state.ts";
import { historyParams, validRange } from "./selectors.ts";
import { AuthError } from "./auth.ts";
import type { AppState } from "./types.ts";
import type { Api } from "./api.ts";

export function bindHistoryControls(
  state: AppState,
  api: Api,
  loadHistory: () => Promise<void>,
  renderHistory: () => void,
  toast: (message: string) => void,
) {
  $("#history-host").addEventListener("change", (event) => {
    state.historyHost = inputValue(event);
    void loadHistory();
  });
  $("#range-buttons").addEventListener("click", (event) => {
    const button = closest<HTMLButtonElement>(
      event.target,
      "button[data-hours]",
    );
    if (!button) return;
    state.hours = Number(button.dataset.hours);
    state.custom = null;
    $$("#range-buttons button").forEach((b) => {
      b.classList.toggle("selected", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    });
    $("#custom-range").hidden = true;
    $("#custom-range-toggle").setAttribute("aria-expanded", "false");
    void loadHistory();
  });
  $("#custom-range-toggle").addEventListener("click", () => {
    const form = $("#custom-range");
    form.hidden = !form.hidden;
    $("#custom-range-toggle").setAttribute(
      "aria-expanded",
      String(!form.hidden),
    );
    if (!form.hidden) {
      const now = new Date(nowSeconds(state) * 1000);
      $<HTMLInputElement>("#range-end").max = dateInput(now);
      if (!state.custom) {
        $<HTMLInputElement>("#range-start").value = dateInput(
          new Date(now.getTime() - 6 * 3600000),
        );
        $<HTMLInputElement>("#range-end").value = dateInput(now);
      }
      $("#range-start").focus();
    }
  });
  $("#custom-range").addEventListener("submit", (event) => {
    event.preventDefault();
    const start =
      new Date($<HTMLInputElement>("#range-start").value).getTime() / 1000;
    const end =
      new Date($<HTMLInputElement>("#range-end").value).getTime() / 1000;
    if (!validRange({ start, end }, nowSeconds(state))) {
      toast(
        "请选择有效时间范围：结束晚于开始，不超过 31 天，且不晚于当前时间。",
      );
      return;
    }
    state.custom = { start, end };
    $$("#range-buttons button").forEach((b) => {
      const selected = b.id === "custom-range-toggle";
      b.classList.toggle("selected", selected);
      b.setAttribute("aria-pressed", String(selected));
    });
    void loadHistory();
  });
  $$("[data-metric]").forEach((button) =>
    button.addEventListener("click", () => {
      const metric = button.dataset.metric;
      if (metric !== "compute" && metric !== "memory") return;
      state.metric = metric;
      $$("[data-metric]").forEach((b) => {
        b.classList.toggle("selected", b === button);
        b.setAttribute("aria-pressed", String(b === button));
      });
      renderHistory();
    }),
  );
  $("#export-button").addEventListener("click", async () => {
    const button = $<HTMLButtonElement>("#export-button");
    button.disabled = true;
    try {
      const blob = await api.exportCsv(
        historyParams(state, nowSeconds(state), true),
      );
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "hqlab-usage.csv";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("时段使用率统计已导出");
    } catch (error) {
      if (!(error instanceof AuthError))
        toast(`导出失败：${errorMessage(error)}`);
    } finally {
      button.disabled = !state.history;
    }
  });
}
