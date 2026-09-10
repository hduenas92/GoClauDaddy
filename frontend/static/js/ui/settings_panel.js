import { api } from "../api/http.js";

/**
 * Model + permission-mode dropdowns for the active conversation. Changes are
 * PATCHed straight to the conversation row — chat_socket.py always reads the
 * conversation's stored settings fresh on every send, so nothing else needs
 * to be told about the change.
 */
export async function mountSettingsPanel(root, conversation) {
  const config = await api.getConfig();

  root.innerHTML = `
    <label>Model
      <select id="model-select"></select>
    </label>
    <label>Permission mode
      <select id="permission-select">
        <option value="">(default)</option>
      </select>
    </label>
  `;

  const modelSelect = root.querySelector("#model-select");
  config.models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.value;
    opt.textContent = m.label;
    modelSelect.appendChild(opt);
  });
  modelSelect.value = conversation.model;

  const permissionSelect = root.querySelector("#permission-select");
  config.permission_modes.forEach((mode) => {
    const opt = document.createElement("option");
    opt.value = mode;
    opt.textContent = mode;
    permissionSelect.appendChild(opt);
  });
  permissionSelect.value = conversation.permission_mode || "";

  modelSelect.addEventListener("change", () => {
    api.updateConversationSettings(conversation.id, { model: modelSelect.value });
  });
  permissionSelect.addEventListener("change", () => {
    api.updateConversationSettings(conversation.id, {
      permission_mode: permissionSelect.value || null,
    });
  });
}
