function readJsonTargetListResponse(value) {
  if (!Array.isArray(value)) {
    throw new Error("CDP /json/list did not return an array");
  }

  return value;
}

export async function listTargets(cdpBaseUrl) {
  const response = await fetch(new URL("/json/list", cdpBaseUrl));
  if (!response.ok) {
    throw new Error(`failed to list CDP targets: ${response.status} ${response.statusText}`);
  }

  return readJsonTargetListResponse(await response.json());
}

export function selectChartTarget(targets, { targetId, chartMatch } = {}) {
  const pages = targets.filter((target) => target?.type === "page");
  const chartPages = pages.filter((target) => typeof target?.url === "string" && target.url.includes("/chart/"));
  const pool = chartPages.length > 0 ? chartPages : pages.length > 0 ? pages : targets;

  if (targetId) {
    const exact = pool.find((target) => target.id === targetId);
    if (exact) return exact;
  }

  if (chartMatch) {
    const needle = chartMatch.toLowerCase();
    const matched = pool.find((target) => {
      const haystack = [target.id, target.title, target.url].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(needle);
    });
    if (matched) return matched;
  }

  return pool[0] ?? null;
}

function createTimeoutError(message) {
  const error = new Error(message);
  error.name = "TimeoutError";
  return error;
}

export class CdpSession {
  constructor(webSocket) {
    this.webSocket = webSocket;
    this.nextId = 1;
    this.pending = new Map();
    this.closed = false;

    this.webSocket.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id != null) {
        const entry = this.pending.get(payload.id);
        if (!entry) return;
        this.pending.delete(payload.id);
        if (payload.error) {
          entry.reject(new Error(payload.error.message ?? "CDP command failed"));
        } else {
          entry.resolve(payload.result);
        }
      }
    });

    this.webSocket.addEventListener("close", () => {
      this.closed = true;
      for (const [, entry] of this.pending) {
        entry.reject(new Error("CDP socket closed"));
      }
      this.pending.clear();
    });

    this.webSocket.addEventListener("error", () => {
      if (!this.closed) {
        for (const [, entry] of this.pending) {
          entry.reject(new Error("CDP socket error"));
        }
        this.pending.clear();
      }
    });
  }

  static async connect(webSocketDebuggerUrl, { timeoutMs = 10000 } = {}) {
    const socket = new WebSocket(webSocketDebuggerUrl);
    await Promise.race([
      new Promise((resolve, reject) => {
        socket.addEventListener("open", resolve, { once: true });
        socket.addEventListener("error", () => reject(new Error(`failed to open CDP websocket: ${webSocketDebuggerUrl}`)), {
          once: true,
        });
      }),
      new Promise((_, reject) => setTimeout(() => reject(createTimeoutError("timed out opening CDP websocket")), timeoutMs)),
    ]);

    const session = new CdpSession(socket);
    await session.call("Runtime.enable");
    return session;
  }

  call(method, params = {}, { timeoutMs = 10000 } = {}) {
    if (this.closed) {
      return Promise.reject(new Error("CDP session is closed"));
    }

    const id = this.nextId++;
    const message = JSON.stringify({ id, method, params });
    this.webSocket.send(message);

    return Promise.race([
      new Promise((resolve, reject) => {
        this.pending.set(id, { resolve, reject });
      }),
      new Promise((_, reject) => setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
        }
        reject(createTimeoutError(`timed out calling CDP method ${method}`));
      }, timeoutMs)),
    ]);
  }

  async evaluate(expression, { timeoutMs = 10000 } = {}) {
    const result = await this.call(
      "Runtime.evaluate",
      {
        expression,
        returnByValue: true,
        awaitPromise: true,
        userGesture: true,
        generatePreview: false,
      },
      { timeoutMs },
    );

    if (result?.exceptionDetails) {
      const description =
        result.exceptionDetails.exception?.description ??
        result.exceptionDetails.exception?.value ??
        result.exceptionDetails.text ??
        "CDP evaluation failed";
      const message = description;
      throw new Error(message);
    }

    return result?.result?.value;
  }

  close() {
    this.closed = true;
    this.webSocket.close();
  }
}

export async function snapshotTarget(webSocketDebuggerUrl, expression, options = {}) {
  const session = await CdpSession.connect(webSocketDebuggerUrl, options);
  try {
    return await session.evaluate(expression, options);
  } finally {
    session.close();
  }
}
