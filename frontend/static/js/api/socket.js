/** Thin WebSocket wrapper: one connection per conversation, JSON in/out, simple event dispatch. */

import { api } from "./http.js";
import { showErrorToast } from "../ui/modal.js";
import * as storage from "../state/storage.js";

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
      // N3. `_close` used to be emitted BEFORE intent was known, so a deliberate
      // conversation switch (main.js:65 calls close()) was indistinguishable
      // from a genuine drop. Any indicator built on `_close` therefore flashed
      // on every switch — worse than no indicator, because it teaches the user
      // to ignore the real one.
      //
      // `_close` keeps its existing semantics and now carries the intent, so
      // current listeners are unaffected. `_disconnected` is the new, opt-in
      // signal and fires ONLY on an unintended drop. Consumers that want to
      // tell the user something is wrong should listen to `_disconnected`.
      const intentional = this._intentionalClose;
      this._emit("_close", { intentional });
      if (!intentional) {
        this._emit("_disconnected", {});
        this._scheduleReconnect();
      }
    });
    // The `_error` emit that used to live here is deliberately GONE rather than
    // left dead. It had no listener anywhere in the tree, and a WebSocket error
    // is always followed by a close event — so `_disconnected` already covers
    // every case a consumer would want, without a second event that fires at a
    // subtly different time. connect()'s own reject path below is a separate
    // one-shot listener and is unaffected.
    return new Promise((resolve, reject) => {
      this.ws.addEventListener("open", () => {
        this._reconnectDelay = 1000; // reset on successful connect
        resolve();
      }, { once: true });
      this.ws.addEventListener("error", (e) => reject(e), { once: true });
      // A close BEFORE the first `open` is a refused connection (a proxy or
      // server answered the upgrade with an error). Without this the promise
      // never settles: switchToConversation() hangs forever, the composer never
      // mounts and nothing is shown (P2-B E2). Reject so the caller can show
      // the retry UI instead.
      this.ws.addEventListener("close", () => {
        reject(new Error("Chat connection failed before it opened."));
      }, { once: true });
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
    try { storage.removeItem(this._pendingKey()); } catch { /* ignore */ }
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
    try { hadPending = !!storage.getItem(key); } catch { hadPending = false; }
    try {
      const { conversation } = await api.getConversation(this.conversationId);
      if (conversation.status === "busy") {
        this._emit("_resync_running", {});
      } else if (hadPending) {
        this._clearPending();
        this._emit("_resync_done", {});
      }
    } catch {
      // Best-effort check, but it must not fail silently: if we can't tell
      // whether a message sent from this tab actually finished, the user
      // needs to know rather than just trust an unconfirmed reply.
      showErrorToast("Couldn't confirm your last message went through — refresh the page to check.");
    }
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
    try { storage.setItem(this._pendingKey(), "1"); } catch { /* ignore */ }
  }

  /**
   * N7. Returns whether the stop was actually sent.
   *
   * Pressing Stop on a dead socket is exactly what a user does when the app
   * looks hung, so this was the most likely route to an unhandled throw in the
   * whole module. It does NOT throw — unlike send(), whose caller has an
   * optimistic bubble to undo and therefore needs to know loudly. A stop that
   * cannot be delivered has nothing to roll back: the turn is already over,
   * whatever the UI currently believes.
   *
   * Note WebSocket.send() would not have thrown on a CLOSING/CLOSED socket
   * anyway — it discards the frame silently — so the old code's real failure
   * mode was a stop that vanished, and a throw only when this.ws was null.
   */
  stop() {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ type: "stop" }));
    return true;
  }

  approve() { this.ws?.send(JSON.stringify({ type: "approve" })); }
  deny()    { this.ws?.send(JSON.stringify({ type: "deny" })); }

  /** WCAG 2.2.1: ask the SERVER for more time on a pending approval. Returns
   * whether the frame was actually sent — like stop(), this must not throw on
   * a dead socket, because the button is exactly what a user presses when the
   * modal has been up long enough to wonder whether anything still works. */
  extendApproval() {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ type: "approval_extend" }));
    return true;
  }

  close() {
    this._intentionalClose = true;
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
    this.ws?.close();
  }
}
