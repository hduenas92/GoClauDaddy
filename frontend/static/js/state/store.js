/** Minimal pub-sub store. UI modules subscribe and render; actions.js is the only writer. */

const state = {
  projects: [], // list from GET /api/projects
  activeProjectId: null, // null = "all conversations" view
  conversations: [], // list from GET /api/conversations
  activeConversationId: null,
  messages: [], // messages of the active conversation
  streaming: false,
};

const listeners = new Set();

export function getState() {
  return state;
}

export function setState(patch) {
  Object.assign(state, patch);
  listeners.forEach((fn) => fn(state));
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
