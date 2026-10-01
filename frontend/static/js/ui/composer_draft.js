/**
 * Per-conversation composer draft autosave (F1).
 *
 * Storage is localStorage, the same layer the codebase already uses for
 * per-user UI prefs (gca_theme at settings_panel.js, gca_sb_open in main.js).
 * Every access is wrapped in try/catch: in private mode localStorage throws on
 * access, and a draft that cannot be saved must never break the composer.
 *
 * A draft that is only whitespace is not saved — saveDraft() removes the key
 * instead, so an empty composer and a whitespace-only composer both mean
 * "nothing to restore".
 */

import * as storage from "../state/storage.js";

const PREFIX = "gca_draft_";

export function draftKey(conversationId) {
  return PREFIX + conversationId;
}

export function loadDraft(conversationId) {
  if (!conversationId) return "";
  try {
    return storage.getItem(draftKey(conversationId)) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(conversationId, text) {
  if (!conversationId) return;
  try {
    if (text && text.trim()) {
      storage.setItem(draftKey(conversationId), text);
    } else {
      storage.removeItem(draftKey(conversationId));
    }
  } catch {
    /* localStorage unavailable — the draft simply will not survive a reload */
  }
}

export function clearDraft(conversationId) {
  if (!conversationId) return;
  try {
    storage.removeItem(draftKey(conversationId));
  } catch {
    /* ignore */
  }
}
