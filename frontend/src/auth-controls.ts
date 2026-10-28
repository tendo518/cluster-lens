import { $, errorMessage } from "./dom.ts";
import { AuthError, AUTH_REVISION_KEY, TOKEN_STORAGE_KEY } from "./auth.ts";
import type { Auth } from "./auth.ts";
import type { Api } from "./api.ts";
import type { Controller } from "./controller.ts";

export function requireAuth() {
  $("#logout-button").hidden = true;
  const detail = $<HTMLDialogElement>("#node-dialog"),
    dialog = $<HTMLDialogElement>("#auth-dialog");
  if (detail.open) detail.close();
  if (!dialog.open) dialog.showModal();
}
export function bindAuthControls(
  auth: Auth,
  api: Api,
  controller: Controller,
  toast: (message: string) => void,
) {
  $("#logout-button").addEventListener("click", () => {
    auth.clear();
    location.reload();
  });
  window.addEventListener("storage", (event) => {
    try {
      if (
        event.storageArea !== localStorage ||
        ![TOKEN_STORAGE_KEY, AUTH_REVISION_KEY, null].includes(event.key)
      )
        return;
    } catch {
      return;
    }
    auth.sync();
  });
  $("#auth-dialog").addEventListener("cancel", (event) =>
    event.preventDefault(),
  );
  $("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $<HTMLButtonElement>("#auth-form button");
    button.disabled = true;
    const snapshot = auth.beginLogin(
      $<HTMLInputElement>("#access-token").value.trim(),
    );
    $("#auth-error").hidden = true;
    try {
      await api.overview();
      if (!auth.completeLogin(snapshot))
        toast(
          "浏览器无法持久保存令牌，当前会话仍可使用；重新打开可能需要再次登录。",
        );
      $<HTMLInputElement>("#access-token").value = "";
      $<HTMLDialogElement>("#auth-dialog").close();
      await controller.refreshOverview(true);
    } catch (error) {
      $("#auth-error").textContent =
        error instanceof AuthError
          ? "令牌无效，请重新输入。"
          : `无法连接服务：${errorMessage(error)}`;
      $("#auth-error").hidden = false;
    } finally {
      button.disabled = false;
    }
  });
}
