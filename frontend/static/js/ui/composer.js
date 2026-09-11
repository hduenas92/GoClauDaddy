import { api } from "../api/http.js";
import { getState } from "../state/store.js";
import { setStreaming } from "../state/actions.js";

export function mountComposer(root, socket, chatPane) {
  root.innerHTML = `
    <div id="attachment-strip"></div>
    <div id="composer-row">
      <button id="composer-attach" title="Attach file">📎</button>
      <textarea id="composer-input" rows="2" placeholder="Message ClaudioUI… (drag files, or paste an image)"></textarea>
      <button id="composer-send">Send</button>
      <button id="composer-stop" hidden>Stop</button>
    </div>
    <input type="file" id="composer-file-input" multiple hidden>
  `;
  const input = root.querySelector("#composer-input");
  const sendBtn = root.querySelector("#composer-send");
  const stopBtn = root.querySelector("#composer-stop");
  const strip = root.querySelector("#attachment-strip");
  const fileInput = root.querySelector("#composer-file-input");
  const attachBtn = root.querySelector("#composer-attach");

  const pending = []; // {id, name}

  function renderStrip() {
    strip.innerHTML = "";
    pending.forEach((att) => {
      const chip = document.createElement("span");
      chip.className = "attachment-chip";
      chip.textContent = att.name;
      const rm = document.createElement("button");
      rm.textContent = "✕";
      rm.addEventListener("click", () => {
        pending.splice(pending.indexOf(att), 1);
        renderStrip();
        // Best-effort: the file was already uploaded and persisted when attached.
        // prune_orphans() remains the safety net if this fails (e.g. page closed mid-removal).
        api.deleteAttachment(att.id).catch(() => {});
      });
      chip.appendChild(rm);
      strip.appendChild(chip);
    });
  }

  async function uploadFile(file) {
    const conversationId = getState().activeConversationId;
    try {
      const att = await api.uploadAttachment(conversationId, file);
      pending.push({ id: att.id, name: att.original_name });
      renderStrip();
    } catch (e) {
      alert(`Could not attach ${file.name}: ${e.message}`);
    }
  }

  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    Array.from(fileInput.files || []).forEach(uploadFile);
    fileInput.value = "";
  });
  input.addEventListener("dragover", (e) => e.preventDefault());
  input.addEventListener("drop", (e) => {
    e.preventDefault();
    Array.from(e.dataTransfer.files || []).forEach(uploadFile);
  });
  input.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) {
      e.preventDefault();
      files.forEach(uploadFile);
    }
  });

  function send() {
    const text = input.value.trim();
    if ((!text && pending.length === 0) || getState().streaming) return;
    chatPane.appendUserMessage(text || "(attachment)");
    chatPane.resetForNewTurn();
    setStreaming(true);
    sendBtn.hidden = true;
    stopBtn.hidden = false;
    socket.send(text, {
      model: getState().model,
      attachment_ids: pending.map((a) => a.id),
    });
    input.value = "";
    pending.length = 0;
    renderStrip();
  }

  function stop() {
    socket.stop();
  }

  socket.on("done", () => {
    sendBtn.hidden = false;
    stopBtn.hidden = true;
  });

  sendBtn.addEventListener("click", send);
  stopBtn.addEventListener("click", stop);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
}
