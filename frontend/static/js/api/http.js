/** Thin fetch wrapper for the REST endpoints (everything except live chat streaming). */

async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    // `detail` is FastAPI's own shape for HTTPException, and most of this
    // app's routers raise HTTPException rather than hand-rolling {error}.
    // Without this the message the router wrote was thrown away and the user
    // got "POST /api/... failed (503)" instead of the sentence explaining why.
    // Strictly widening: anything that reached the generic string before still
    // reaches it, because it had neither key.
    throw new Error(data.error || data.detail || `The request failed (${res.status}). Try again; if it keeps failing, open the LOG panel.`);
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
  autoTitleConversation: (id) => req("POST", `/api/conversations/${id}/auto-title`),
  exportConversation: (id) => req("GET", `/api/conversations/${id}/export`),

  // encodeURIComponent is not optional: `q` is arbitrary text a user typed and
  // will contain &, # and +, each of which silently changes the query string.
  // The server returns { results: [...], truncated: bool }; `truncated` is
  // computed BEFORE slicing, so it is an honest "there are more".
  searchMessages: (q, limit) =>
    req("GET", `/api/search?q=${encodeURIComponent(q)}${limit ? `&limit=${limit}` : ""}`),

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
      throw new Error(data.error || data.detail || `Upload failed (${res.status}). Try again, or use a smaller file.`);
    }
    return res.json();
  },
  deleteAttachment: (id) => req("DELETE", `/api/attachments/${id}`),

  assessMessage: (conversationId, message) =>
    req("POST", "/api/conversations/assess", { conversation_id: conversationId, message }),

  listFlowTemplates: () => req("GET", "/api/flow-templates"),
  createFlowTemplate: (body) => req("POST", "/api/flow-templates", body),
  updateFlowTemplate: (id, body) => req("PUT", `/api/flow-templates/${id}`, body),
  deleteFlowTemplate: (id) => req("DELETE", `/api/flow-templates/${id}`),
};
