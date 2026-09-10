import { api } from "../api/http.js";
import { getState, setState } from "./store.js";

export function setStreaming(v) {
  setState({ streaming: v });
}

export async function loadProjects() {
  const projects = await api.listProjects();
  setState({ projects });
  return projects;
}

export async function createProject(name, workingDir, systemPrompt) {
  const project = await api.createProject({ name, working_dir: workingDir, system_prompt: systemPrompt || null });
  await loadProjects();
  return project;
}

export async function deleteProject(projectId) {
  await api.deleteProject(projectId);
  await loadProjects();
  if (getState().activeProjectId === projectId) {
    setState({ activeProjectId: null });
  }
}

export function selectProject(projectId) {
  setState({ activeProjectId: projectId });
}

export async function loadConversations() {
  const { activeProjectId } = getState();
  const conversations = await api.listConversations(activeProjectId || undefined);
  setState({ conversations });
  return conversations;
}

export async function createConversation() {
  const { activeProjectId } = getState();
  const conv = await api.createConversation({ project_id: activeProjectId || null });
  const conversations = await loadConversations();
  setState({ conversations, activeConversationId: conv.id, messages: [] });
  return conv;
}

export async function selectConversation(conversationId) {
  const { conversation, messages } = await api.getConversation(conversationId);
  setState({ activeConversationId: conversation.id, messages });
  return conversation;
}

export async function renameConversation(conversationId, name) {
  await api.renameConversation(conversationId, name);
  await loadConversations();
}

export async function deleteConversation(conversationId) {
  await api.deleteConversation(conversationId);
  const conversations = await loadConversations();
  if (getState().activeConversationId === conversationId) {
    setState({ activeConversationId: null, messages: [] });
  }
  return conversations;
}
