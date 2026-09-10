import { api } from "../api/http.js";
import { getState, subscribe } from "../state/store.js";
import { createProject, deleteProject, selectProject } from "../state/actions.js";

export function mountSidebarProjects(root, onSwitchProject) {
  root.innerHTML = `
    <button id="new-project-btn">+ New Project</button>
    <ul id="project-list">
      <li class="project-item" data-all="1">All conversations</li>
    </ul>
  `;
  const listEl = root.querySelector("#project-list");

  root.querySelector("#new-project-btn").addEventListener("click", async () => {
    const { path } = await api.browseDirectory();
    if (!path) return;
    const name = prompt("Project name", path.split(/[\\/]/).pop());
    if (!name || !name.trim()) return;
    await createProject(name.trim(), path);
    render();
  });

  function render() {
    const { projects, activeProjectId } = getState();
    listEl.innerHTML = `<li class="project-item${activeProjectId ? "" : " active"}" data-all="1">All conversations</li>`;
    projects.forEach((p) => {
      const li = document.createElement("li");
      li.className = "project-item" + (p.id === activeProjectId ? " active" : "");
      li.innerHTML = `
        <span class="project-name" title="${escapeHtml(p.working_dir)}">${escapeHtml(p.name)}</span>
        <button class="project-delete" title="Delete project">✕</button>
      `;
      li.querySelector(".project-name").addEventListener("click", () => {
        selectProject(p.id);
        onSwitchProject(p.id);
        render();
      });
      li.querySelector(".project-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (confirm(`Delete project "${p.name}"? Conversations become standalone.`)) {
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

  subscribe(render);
  render();
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
