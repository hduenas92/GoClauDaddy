/** Thin WebSocket wrapper: one connection per conversation, JSON in/out, simple event dispatch. */

export class ChatSocket {
  constructor(conversationId) {
    this.conversationId = conversationId;
    this.ws = null;
    this.listeners = new Map(); // eventType -> Set<fn>
  }

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/chat/${this.conversationId}`);
    this.ws.addEventListener("message", (e) => {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch {
        return;
      }
      this._emit(data.type, data);
    });
    this.ws.addEventListener("close", () => this._emit("_close", {}));
    this.ws.addEventListener("error", () => this._emit("_error", {}));
    return new Promise((resolve, reject) => {
      this.ws.addEventListener("open", () => resolve(), { once: true });
      this.ws.addEventListener("error", (e) => reject(e), { once: true });
    });
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
    this.ws.send(JSON.stringify({ type: "send", message, ...opts }));
  }

  stop() {
    this.ws.send(JSON.stringify({ type: "stop" }));
  }

  close() {
    this.ws?.close();
  }
}
