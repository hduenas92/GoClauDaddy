import { ChatSocket } from "./api/socket.js";
import {
  loadConversations,
  loadProjects,
  selectConversation,
  createConversation,
} from "./state/actions.js";
import { getState } from "./state/store.js";
import { mountChatPane } from "./ui/chat_pane.js";
import { mountComposer } from "./ui/composer.js";
import { mountSidebarConversations } from "./ui/sidebar_conversations.js";
import { mountSidebarProjects } from "./ui/sidebar_projects.js";
import { mountSettingsPanel } from "./ui/settings_panel.js";

let currentSocket = null;

async function switchToConversation(id, chatPane, composerRoot, settingsRoot) {
  if (currentSocket) currentSocket.close();

  const conversation = await selectConversation(id);
  chatPane.renderHistory(getState().messages);
  await mountSettingsPanel(settingsRoot, conversation);

  const socket = new ChatSocket(id);
  await socket.connect();
  currentSocket = socket;
  chatPane.bindSocket(socket);
  mountComposer(composerRoot, socket, chatPane);
}

async function boot() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div id="sidebar">
      <div id="sidebar-projects"></div>
      <hr class="sidebar-sep">
      <div id="sidebar-conversations"></div>
    </div>
    <div id="chat-area">
      <div id="chat-header"><div id="settings-panel"></div></div>
      <div id="chat-scroll"></div>
      <div id="composer"></div>
    </div>
  `;

  const chatPane = mountChatPane(document.getElementById("chat-scroll"));
  const composerRoot = document.getElementById("composer");
  const settingsRoot = document.getElementById("settings-panel");

  let conversations;
  try {
    await loadProjects();
    conversations = await loadConversations();
  } catch {
    app.innerHTML = `<div id="boot-msg">Cannot reach backend. Is ClaudioUI running?</div>`;
    return;
  }

  mountSidebarConversations(document.getElementById("sidebar-conversations"), (id) =>
    switchToConversation(id, chatPane, composerRoot, settingsRoot)
  );

  mountSidebarProjects(document.getElementById("sidebar-projects"), async () => {
    conversations = await loadConversations();
    if (conversations.length > 0) {
      await switchToConversation(conversations[0].id, chatPane, composerRoot, settingsRoot);
    } else {
      chatPane.clear();
      if (currentSocket) currentSocket.close();
    }
  });

  if (conversations.length === 0) {
    const conv = await createConversation();
    await switchToConversation(conv.id, chatPane, composerRoot, settingsRoot);
  } else {
    await switchToConversation(conversations[0].id, chatPane, composerRoot, settingsRoot);
  }
}

boot();
