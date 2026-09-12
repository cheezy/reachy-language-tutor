# The `/rpc` control surface and the daemon relay (D18)

This records an answer that cost two attempts to reach, so the next person inherits
the reasoning rather than re-tracing it. It follows the house habit (`docs/SETUP.md`,
the `custom_app_url` comment in `main.py`) of writing down *why* a constraint exists.

## The question

The app exposes a JSON-RPC control surface at `/rpc` (`console.py`, from line 641):
`conversation.say`, `conversation.interrupt`, `conversation.mic {muted}`, and
`backend.config`. D15 bound the app's own web server to loopback to keep that surface
off the household LAN. The D15 round-2 reviewer traced a *second* route to the same
methods — the SDK daemon relaying JSON-RPC into the app over a WebRTC DataChannel —
and could not confirm it end to end, because the last link is a native signalling
server it could not read. D18 was that question: **can a host on the same LAN invoke
`conversation.mic` on the app through the daemon, with no credential?**

## The answer: yes

Confirmed two ways, against the SDK version actually installed (`reachy-mini==1.10.0`,
not the 1.8.0 the original trace used — every line below was re-resolved).

**The code path is credential-free from the DataChannel inward.**

- `reachy_mini/daemon/jsonrpc_relay.py:90` — only `apps.*` is handled locally; every
  other namespace (so `conversation.*`) is relayed to the running app's `/rpc`.
- `reachy_mini/daemon/backend/abstract.py:3031` — any DataChannel frame that
  `looks_like_jsonrpc` is handed straight to that relay. There is **no** token, pairing,
  or origin check anywhere on the path (the only auth gate in the daemon, the `hf_token`
  at `daemon.py:206`, guards the *central* internet relay, not this).
- `reachy_mini/daemon/jsonrpc_relay.py:278` — `_rpc_ws_url` maps `0.0.0.0` → `127.0.0.1`
  and the daemon connects on the loopback regardless. **So binding the app to loopback
  does not keep the daemon out; it was never meant to.** That is the crux: the D15 change
  cannot close this route by construction.

**The one remaining gate — the signalling server's admission — is open, and this is the
part the previous attempt could not read.** The WebRTC media server tells GStreamer's
`webrtcsink` to run its own signalling server (`media/media_server.py:368`,
`run-signalling-server=True`). Its `signalling-server-host` property defaults to
`0.0.0.0` and nothing in the SDK narrows it, so the signalling WebSocket listens on all
interfaces on port 8443. Verified empirically against the live desktop daemon: an
anonymous WebSocket client, connecting **from the machine's LAN IP** (not loopback) with
no token, receives a `welcome` peer id and a producer list naming the robot
(`{"name":"reachymini"}`). There is no admission control at the handshake.

The one link not demonstrated is the compiled ICE/DataChannel negotiation completing for
a cross-host peer — that logic lives inside `libgstrswebrtc.dylib` (Rust, opaque to
source reading), and finishing it would mean building a working microphone-unmute exploit,
which was deliberately not done. Every gate up to that point is confirmed open, so the
honest verdict is **yes, with that single caveat named** rather than a bare yes.

Negative result kept so nobody re-checks it: `/ws/sdk` is **not** a route to this
(`reachy_mini/io/ws_server.py:146` validates against `message_adapter` only, no JSON-RPC
branch) — still true in 1.10.

## Why this app cannot fix it by moving or hiding the surface

A relayed call and a legitimate one arrive identically: on loopback, from the daemon, at
`/rpc`. The app has nothing to tell them apart by. So no bind address and no relocation
helps — confirmed the hard way, because the D15 loopback bind not only fails to close this
route but **breaks the dashboard on a Wireless unit** (the dashboard loads the app by the
robot's LAN IP; see the D15-regression defect). The capability at the far end is real:
`conversation.mic {muted:false}` unmutes a microphone in a family home, and
`backend.config` is a *writer* that repoints the speech backend at a host of the caller's
choosing.

## Decision recorded

- **Report upstream.** The chain and the affected version (`reachy-mini==1.10.0`,
  `gst-plugin-webrtc` `0.15.3`) go to `pollen-robotics/reachy_mini`: the daemon relays
  unauthenticated JSON-RPC from a DataChannel into the app, and the signalling server that
  admits the peer binds all interfaces with no admission control. Report the chain, not a
  weaponized path. *(Filing this issue is an outward action left to a maintainer of this
  repo; the text above is the report.)*
- **Keep shipping `conversation.mic` / `backend.config` for now.** They drive the app's own
  settings dashboard (`static/js/api.js`); removing them breaks legitimate function, and
  removal is the *only* in-app lever that would change anything, since the app cannot
  distinguish the calls. Accepting that in writing is the honest interim state: no unit is
  deployed yet.
- **The real fix is authenticating `/rpc`** — a per-instance bearer token from `.env` (per
  `CLAUDE.md`, never committed) plus an `Origin` check on the WebSocket handshake. That one
  change closes this relay route, the cross-origin-WebSocket route, and lets the bind return
  to LAN-reachable so the dashboard works. Filed as the `/rpc` authentication defect; not
  landed here, because D18's remit was to answer the question, not to build the mitigation.
