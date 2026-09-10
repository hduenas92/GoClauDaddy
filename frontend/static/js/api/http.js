/** Thin fetch wrapper for the REST endpoints (everything except live chat streaming). */

async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `${method} ${path} failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  getConfig: () => req("GET", "/api/config"),
  listConversations: (projectId) =>
    req("GET", `/api/conversations${projectId ? `?project_id=${projectId}` : ""}`),
  createConversation: (body = {}) => req("POST", "/api/conversations", body),
  getConversation: (id) => req("GET", `/api/conversations/${id}`),
  renameConversation: (id, name) => req("PATCH", `/api/conversations/${id}/rename`, { name }),
  updateConversationSettings: (id, settings) =>
    req("PATCH", `/api/conversations/${id}/settings`, settings),
  deleteConversation: (id) => req("DELETE", `/api/conversations/${id}`),

  listProjects: () => req("GET", "/api/projects"),
  createProject: (body) => req("POST", "/api/projects", body),
  updateProject: (id, body) => req("PATCH", `/api/projects/${id}`, body),
  deleteProject: (id) => req("DELETE", `/api/projects/${id}`),
  browseDirectory: (initialDir) =>
    req("POST", `/api/projects/browse-directory${initialDir ? `?initial_dir=${encodeURIComponent(initialDir)}` : ""}`),

  async uploadAttachment(conversationId, file) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/attachments?conversation_id=${encodeURIComponent(conversationId)}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || data.detail || `upload failed (${res.status})`);
    }
    return res.json();
  },
  deleteAttachment: (id) => req("DELETE", `/api/attachments/${id}`),
};
