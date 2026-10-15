// WebSocket client for the dev-server hub (docs/SPEC.md §5): hello handshake,
// reconnect with backoff, and a bounded send queue while disconnected.

export interface LinkMessage {
  t: string;
  [key: string]: unknown;
}

export interface LinkClientOptions {
  port: number;
  token: string;
  /** Free-form client description sent in `hello`. */
  client: string;
  /** Called with every message after the handshake. */
  onMessage(message: LinkMessage): void;
  /** Called after each successful handshake. */
  onConnect?(): void;
}

const PROTOCOL = 1;
const MAX_QUEUE = 500;
const MIN_BACKOFF = 250;
const MAX_BACKOFF = 5000;

export class LinkClient {
  private socket: WebSocket | null = null;
  private welcomed = false;
  private closed = false;
  private backoff = MIN_BACKOFF;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly queue: string[] = [];
  /** Our peer id once welcomed. */
  peer: string | null = null;

  constructor(private readonly options: LinkClientOptions) {}

  get connected(): boolean {
    return this.welcomed && this.socket?.readyState === WebSocket.OPEN;
  }

  connect(): void {
    if (this.closed || this.socket) return;
    let socket: WebSocket;
    try {
      socket = new WebSocket(`ws://127.0.0.1:${this.options.port}`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      socket.send(
        JSON.stringify({ t: 'hello', role: 'game', token: this.options.token, protocol: PROTOCOL, client: this.options.client }),
      );
    };
    socket.onmessage = (event) => {
      let message: LinkMessage;
      try {
        message = JSON.parse(String(event.data)) as LinkMessage;
      } catch {
        return;
      }
      if (!message || typeof message.t !== 'string') return;
      if (!this.welcomed) {
        if (message.t === 'welcome') {
          this.welcomed = true;
          this.peer = typeof message.peer === 'string' ? message.peer : null;
          this.backoff = MIN_BACKOFF;
          this.options.onConnect?.();
          this.flush();
        }
        // 'error' before welcome (bad token/protocol): the hub closes the socket.
        return;
      }
      if (message.t === 'ping') {
        this.send({ t: 'pong' });
        return;
      }
      this.options.onMessage(message);
    };
    socket.onclose = () => {
      this.socket = null;
      this.welcomed = false;
      this.scheduleReconnect();
    };
    socket.onerror = () => {
      // onclose follows; nothing else to do (and logging here would echo into the link).
    };
  }

  /** Sends now if connected, otherwise queues (oldest dropped beyond the limit). */
  send<M extends { t: string }>(message: M): void {
    const text = JSON.stringify(message);
    if (this.connected) {
      this.socket!.send(text);
      return;
    }
    this.queue.push(text);
    if (this.queue.length > MAX_QUEUE) this.queue.shift();
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close();
    this.socket = null;
  }

  private flush(): void {
    while (this.queue.length > 0 && this.connected) this.socket!.send(this.queue.shift()!);
  }

  private scheduleReconnect(): void {
    if (this.closed || this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, this.backoff);
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF);
  }
}
