import { api } from "../api/http.js";
import { getState, subscribe } from "../state/store.js";
import { createProject, deleteProject, loadProjects, selectProject } from "../state/actions.js";
import { showModal, showConfirm, showErrorToast } from "./modal.js";

export function mountSidebarProjects(root, onSwitchProject) {
  root.innerHTML = `
    <button id="new-project-btn">＋ New Project</button>
    <ul id="project-list">
      <li class="project-item" data-all="1">All conversations</li>
    </ul>
  `;
  const listEl = root.querySelector("#project-list");

  root.querySelector("#new-project-btn").addEventListener("click", async () => {
    const result = await showModal({
      title: "New Project",
      fields: [
        { name: "name", label: "Project name", placeholder: "My Project" },
        { name: "working_dir", label: "Working directory (optional)", placeholder: "C:\\Users\\..." },
        { name: "system_prompt", label: "System prompt (optional)", type: "textarea", placeholder: "You are a helpful assistant…" },
      ],
      confirmText: "Create",
    });
    if (!result || !result.name?.trim()) return;
    try {
      await createProject(result.name.trim(), result.working_dir?.trim() || null, result.system_prompt?.trim() || null);
      render();
    } catch (err) {
      showErrorToast(`Couldn't create the project: ${err.message}. Check the name and working directory, then try again.`);
    }
  });

  function render() {
    const { projects, activeProjectId } = getState();
    listEl.innerHTML = `<li class="project-item${activeProjectId ? "" : " active"}" data-all="1">All conversations</li>`;
    projects.forEach((p) => {
      const li = document.createElement("li");
      li.className = "project-item" + (p.id === activeProjectId ? " active" : "");
      li.innerHTML = `
        <span class="project-name" role="button" tabindex="0" title="${escapeHtml(p.working_dir)}">${escapeHtml(p.name)}</span>
        <button class="project-edit" title="Edit project">✎</button>
        <button class="project-delete" title="Delete project">✕</button>
      `;
      const projectName = li.querySelector(".project-name");
      const selectProj = () => {
        selectProject(p.id);
        onSwitchProject(p.id);
        render();
      };
      projectName.addEventListener("click", selectProj);
      projectName.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectProj();
        }
      });
      li.querySelector(".project-edit").addEventListener("click", (e) => {
        e.stopPropagation();
        showEditForm(li, p);
      });
      li.querySelector(".project-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        const ok = await showConfirm({
          message: `Delete project "${p.name}"? Conversations will become standalone.`,
        });
        if (ok) {
          await deleteProject(p.id);
          onSwitchProject(null);
          render();
        }
      });
      listEl.appendChild(li);
    });
    listEl.querySelector('[data-all="1"]').addEventListener("click", () => {
      selectProject(null);
      onSwitchProject(null);
      render();
    });
  }

  function showEditForm(li, p) {
    li.innerHTML = `
      <div class="project-edit-form">
        <input class="pef-name" value="${escapeHtml(p.name)}" placeholder="Name">
        <div class="pef-dir-row">
          <input class="pef-dir" value="${escapeHtml(p.working_dir)}" placeholder="Working dir (optional, absolute path)">
          <button class="pef-clear-dir" title="Clear directory">✕</button>
        </div>
        <textarea class="pef-prompt" rows="3" placeholder="System prompt (optional)">${escapeHtml(p.system_prompt || "")}</textarea>
        <div class="pef-actions">
          <button class="pef-save">Save</button>
          <button class="pef-cancel">Cancel</button>
        </div>
      </div>
    `;
    const nameInput = li.querySelector(".pef-name");
    const dirInput = li.querySelector(".pef-dir");
    const clearDirBtn = li.querySelector(".pef-clear-dir");
    const promptInput = li.querySelector(".pef-prompt");

    clearDirBtn.addEventListener("click", () => { dirInput.value = ""; });

    li.querySelector(".pef-save").addEventListener("click", async () => {
      const patch = {};
      if (nameInput.value.trim()) patch.name = nameInput.value.trim();
      patch.working_dir = dirInput.value.trim() || null;
      patch.system_prompt = promptInput.value.trim() || null;
      try {
        await api.updateProject(p.id, patch);
        await loadProjects();
        render();
      } catch (err) {
        showErrorToast(`Couldn't save changes to the project: ${err.message}. Try again.`);
      }
    });

    li.querySelector(".pef-cancel").addEventListener("click", () => render());
    nameInput.focus();
  }

  subscribe(render);
  render();
}

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
