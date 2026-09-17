/** Thin WebSocket wrapper: one connection per conversation, JSON in/out, simple event dispatch. */

import { api } from "./http.js";

// Terminal event types for a turn — once one of these is observed live, the
// turn is no longer "pending" from this client's point of view.
const TERMINAL_TYPES = new Set(["done", "error", "stopped", "timeout"]);

export class ChatSocket {
  constructor(conversationId) {
    this.conversationId = conversationId;
    this.ws = null;
    this.listeners = new Map(); // eventType -> Set<fn>
    this._intentionalClose = false;
    this._reconnectDelay = 1000;
    this._reconnectTimer = null;
  }

  connect() {
    this._intentionalClose = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/chat/${this.conversationId}`);
    this.ws.addEventListener("message", (e) => {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch {
        return;
      }
      if (TERMINAL_TYPES.has(data.type)) this._clearPending();
      this._emit(data.type, data);
    });
    this.ws.addEventListener("close", () => {
      this._emit("_close", {});
      if (!this._intentionalClose) this._scheduleReconnect();
    });
    this.ws.addEventListener("error", () => this._emit("_error", {}));
    return new Promise((resolve, reject) => {
      this.ws.addEventListener("open", () => {
        this._reconnectDelay = 1000; // reset on successful connect
        resolve();
      }, { once: true });
      this.ws.addEventListener("error", (e) => reject(e), { once: true });
    });
  }

  _scheduleReconnect() {
    if (this._reconnectTimer) return;
    const delay = this._reconnectDelay;
    this._reconnectTimer = setTimeout(async () => {
      this._reconnectTimer = null;
      try {
        // Deliberately does NOT resync: an auto-reconnect after a network blip
        // means the tab never went away, so there is nothing the user missed.
        // Resync is the caller's explicit choice — see checkResync().
        await this.connect();
        this._emit("_reconnect", {});
      } catch {
        this._reconnectDelay = Math.min(delay * 2, 30_000);
        this._scheduleReconnect();
      }
    }, delay);
  }

  _pendingKey() {
    return `gca_pending_${this.conversationId}`;
  }

  _clearPending() {
    try { localStorage.removeItem(this._pendingKey()); } catch { /* ignore */ }
  }

  // Distinguishes "a turn completed while this client was away" from "this
  // conversation was already finished before I left": the localStorage flag is
  // set only when *this* client sent a message and never itself observed that
  // turn's terminal event (done/error/stopped/timeout) — i.e. it navigated away
  // or reloaded mid-turn. A plain switch to an already-finished conversation
  // never sets the flag, so this emits nothing.
  //
  // CALLED BY THE OWNER AFTER LISTENERS ARE BOUND — never from connect().
  // It used to fire inside the `open` handler, which raced its own consumer:
  // main.js binds listeners two lines AFTER `await socket.connect()` resolves,
  // so the emit only landed on a registered listener because the awaited
  // getConversation() round-trip happened to be slower than a synchronous line
  // of JS. That is latency, not ordering. A warm cache or a slower bind would
  // have dropped the event silently, and no test could catch it reliably.
  async checkResync() {
    const key = this._pendingKey();
    let hadPending;
    try { hadPending = !!localStorage.getItem(key); } catch { hadPending = false; }
    try {
      const { conversation } = await api.getConversation(this.conversationId);
      if (conversation.status === "busy") {
        this._emit("_resync_running", {});
      } else if (hadPending) {
        this._clearPending();
        this._emit("_resync_done", {});
      }
    } catch { /* best-effort — no resync event on failure */ }
  }

  on(eventType, fn) {
    if (!this.listeners.has(eventType)) this.listeners.set(eventType, new Set());
    this.listeners.get(eventType).add(fn);
    return () => this.listeners.get(eventType)?.delete(fn);
  }

  _emit(eventType, data) {
    this.listeners.get(eventType)?.forEach((fn) => fn(data));
  }

  send(message, opts = {}) {
    // readyState must be checked explicitly. WebSocket.send() does NOT throw on
    // a CLOSING or CLOSED socket — per spec only CONNECTING throws; the other
    // two silently discard the frame. Measured: readyState 3, send() returned
    // normally, nothing delivered. So neither "send then flag" nor "flag then
    // send" is sufficient on its own — without this guard the pending flag
    // outlives a message that never reached the server, and the next fresh
    // connect reports "Completed while you were away" for a turn that never ran.
    if (this.ws?.readyState !== WebSocket.OPEN) {
      throw new Error("Not connected — message was not sent.");
    }
    this.ws.send(JSON.stringify({ type: "send", message, ...opts }));
    try { localStorage.setItem(this._pendingKey(), "1"); } catch { /* ignore */ }
  }

  stop() {
    this.ws.send(JSON.stringify({ type: "stop" }));
  }

  approve() { this.ws?.send(JSON.stringify({ type: "approve" })); }
  deny()    { this.ws?.send(JSON.stringify({ type: "deny" })); }

  close() {
    this._intentionalClose = true;
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
    this.ws?.close();
  }
}
