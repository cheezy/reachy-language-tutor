/** JSON-RPC-over-WebSocket client for the settings backend (/rpc). */

const DEFAULT_TIMEOUT_MS = 8000;
const TOOL_SPACE_TIMEOUT_MS = 60000;

// How long /rpc may still be coming up after the page loads, and how often to re-ask.
const STARTUP_POLL_MS = 2000;
const STARTUP_DEADLINE_MS = 90000;

const RPC_URL = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/rpc`;

class RpcError extends Error {
  constructor(message, reason, data = {}) {
    super(message || reason || "rpc error");
    // Views branch on error.body.error (the stable reason); keep that contract.
    this.body = { ...data, error: reason };
    this.reason = reason;
  }
}

let socket = null;
let connecting = null;
let rpcCounter = 0;
const pending = new Map(); // id -> { resolve, reject, timer }
const subscribers = new Map(); // method -> Set<cb>

/** Open (or reuse) the shared /rpc socket. Resolves once OPEN, rejects on fail. */
function connect() {
  if (socket && socket.readyState === WebSocket.OPEN) return Promise.resolve();
  if (connecting) return connecting;
  connecting = new Promise((resolve, reject) => {
    let opened = false;
    const ws = new WebSocket(RPC_URL);
    socket = ws;
    ws.onopen = () => {
      opened = true;
      connecting = null;
      resolve();
    };
    ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
    ws.onclose = () => {
      socket = null;
      connecting = null;
      for (const p of pending.values()) {
        clearTimeout(p.timer);
        p.reject(new RpcError("connection closed", "disconnected"));
      }
      pending.clear();
      if (!opened) reject(new RpcError("cannot reach /rpc", "disconnected"));
      // Keep the event stream alive across drops while anyone is listening.
      else if (subscribers.size > 0) setTimeout(() => connect().catch(() => {}), 1000);
    };
  });
  return connecting;
}

function handleMessage(msg) {
  if (msg.id != null && ("result" in msg || "error" in msg)) {
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    clearTimeout(p.timer);
    if (msg.error) p.reject(new RpcError(msg.error.message, msg.error.data?.reason, msg.error.data));
    else p.resolve(msg.result);
    return;
  }
  if (typeof msg.method === "string") {
    const cbs = subscribers.get(msg.method);
    if (cbs) for (const cb of cbs) {
      try {
        cb(msg.params || {});
      } catch (e) {
        console.error(`subscribe(${msg.method}) callback threw:`, e);
      }
    }
  }
}

/**
 * Which methods this surface will actually run, as the server reported them.
 *
 * null means "not asked yet". The server sends `rpc_methods_available` on
 * conversation.status, derived from its own allow-list -- see console.py. We never
 * keep a list of REFUSED methods here: that would be a second copy of the rule, in
 * another language, drifting from the first the day a method is added. What is
 * permitted arrives from the one place that enforces it.
 */
let availableMethods = null;

/** Remember what the server says it will run. Called with every status result. */
function rememberAvailability(status) {
  const listed = status?.rpc_methods_available;
  if (Array.isArray(listed)) availableMethods = new Set(listed);
  return status;
}

/**
 * Whether the server will run this method, as far as we have been told.
 *
 * Unknown -> true. Before the first status arrives we must not disable controls on a
 * guess: a UI that greys out a working button because it has not finished loading is
 * worse than one that lets the call fail honestly.
 */
export function isAvailable(method) {
  return availableMethods === null || availableMethods.has(method);
}

/**
 * Ask the server what it will run, retrying while it is still coming up. Never throws.
 *
 * Retried, not asked once: /rpc can be unready for up to STARTUP_DEADLINE_MS after the
 * page loads -- that is why untilReady exists at all -- and a single failed ask would
 * leave availability unknown for the whole tab session, not merely for the first paint.
 * Unknown is permissive, so that is a session in which every gate reads "available" and
 * clicks reach writers the server refuses. Retrying is what makes the permissive window
 * short rather than indefinite.
 *
 * Still permissive if it never succeeds: the chokepoint in rpcCall and the server's own
 * registrar both still refuse, so the cost of never learning is a worse message, not a
 * writer that runs.
 */
export async function refreshAvailability() {
  if (availableMethods !== null) return availableMethods;
  const deadline = Date.now() + STARTUP_DEADLINE_MS;
  for (;;) {
    try {
      await getStatus();
      return availableMethods;
    } catch {
      if (Date.now() >= deadline) return availableMethods;
    }
    await new Promise((resolve) => setTimeout(resolve, STARTUP_POLL_MS));
  }
}

/** Call a JSON-RPC method and await its result. Rejects with RpcError. */
export async function rpcCall(method, params = {}, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  if (!isAvailable(method)) {
    // Refused here rather than on the wire. The server would refuse it anyway; doing it
    // at the single chokepoint means no control can reach a refused writer however it
    // was wired, and the caller gets the same stable reason string either way.
    throw new RpcError(`${method} is not available over the network`, "not_available_over_the_network");
  }
  await connect();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new RpcError("not connected", "disconnected");
  }
  const id = `ui-${++rpcCounter}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new RpcError(`timed out: ${method}`, "timeout"));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
  });
}

/** Subscribe to a one-way notification (event). Returns an unsubscribe fn. */
export function subscribe(method, cb) {
  let set = subscribers.get(method);
  if (!set) {
    set = new Set();
    subscribers.set(method, set);
  }
  set.add(cb);
  connect().catch(() => {});
  return () => {
    subscribers.get(method)?.delete(cb);
  };
}

/** Retry a request while the backend is still coming up at startup. */
export async function untilReady(requestFn, signal, onRetry) {
  const deadline = Date.now() + STARTUP_DEADLINE_MS;
  let notified = false;
  for (;;) {
    try {
      return await requestFn();
    } catch (error) {
      if (signal.aborted || Date.now() >= deadline) throw error;
      if (!notified) {
        notified = true;
        onRetry?.();
      }
    }
    await new Promise((resolve) => setTimeout(resolve, STARTUP_POLL_MS));
    if (signal.aborted) throw new Error("view unmounted");
  }
}

export const getStatus = () => rpcCall("conversation.status").then(rememberAvailability);

export const listPersonalities = () => rpcCall("personalities.list");
export const loadPersonality = (name) => rpcCall("personalities.load", { name });
export const savePersonality = (payload) => rpcCall("personalities.save", payload);
export const applyPersonality = (name, { persist = false, force = false } = {}) =>
  rpcCall("personalities.apply", { name, persist, force });
export const deletePersonality = (name) => rpcCall("personalities.delete", { name });

export const getMicState = () => rpcCall("conversation.mic", {});
export const setMicMuted = (muted) => rpcCall("conversation.mic", { muted });

export const listVoices = () => rpcCall("voices.list");
export const getCurrentVoice = () => rpcCall("voices.current");
export const applyVoice = (voice) => rpcCall("voices.apply", { voice });

export const saveBackendConfig = (payload) => rpcCall("backend.config", payload);

export const listToolSpaces = () => rpcCall("tool_spaces.list");
export const addToolSpace = (slug) =>
  rpcCall("tool_spaces.add", { slug }, { timeoutMs: TOOL_SPACE_TIMEOUT_MS });
export const removeToolSpace = (slug) =>
  rpcCall("tool_spaces.remove", { slug }, { timeoutMs: TOOL_SPACE_TIMEOUT_MS });

export const getProfileTools = (profile) =>
  rpcCall("profile_tools.get", profile ? { profile } : {});
export const saveProfileTools = (profile, enabledTools) =>
  rpcCall("profile_tools.save", { profile, enabled_tools: enabledTools });
export const resetProfileTools = (profile) =>
  rpcCall("profile_tools.reset", { profile });

/** Backend error codes that need friendlier copy than the raw code. */
const ERROR_MESSAGES = Object.freeze({
  invalid_backend: "Unknown backend selected.",
  empty_key: "An API key is required for this backend.",
  empty_hf_host: "Enter a Hugging Face host.",
  invalid_hf_host: "That Hugging Face host doesn't look right.",
  invalid_hf_port: "That Hugging Face port doesn't look right.",
  invalid_hf_mode: "Unknown Hugging Face mode.",
  missing_hf_session_url: "Couldn't reach the Hugging Face Space. Check it's running.",
  invalid_name: "Enter a valid profile name.",
  invalid_instructions: "Enter personality instructions.",
  profile_exists: "A personality with this name already exists.",
  invalid_tool_space_slug: "Enter a Space in owner/name format.",
  invalid_tool_selection: "One or more selected tools are no longer available.",
  unknown_profile: "That personality is no longer available.",
  missing_voice: "Choose a voice first.",
  profile_locked: "Profile switching is locked by the administrator.",
  profile_in_use: "This personality is active or set to load at startup. Switch to another one first.",
  not_deletable: "This personality can't be deleted.",
  loop_unavailable: "Reachy is still starting up. Try again in a moment.",
  tool_space_not_installed: "That Tool Space is no longer installed.",
  // The network refusal, in the learner's register rather than the protocol's. See
  // docs/rpc-control-surface.md for why these writers are refused at all.
  not_available_over_the_network:
    "This can't be changed from here. Reachy is configured from its own files on the robot.",
  mic_is_read_only: "The microphone can't be switched on or off from here.",
});

/** Map a thrown error to user-facing copy, falling back to its raw message. */
export function describeError(error) {
  const code = error?.body?.error;
  return ERROR_MESSAGES[code] || error?.body?.detail || error?.message || String(error);
}
