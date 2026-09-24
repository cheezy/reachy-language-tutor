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

**Re-checked against 1.11.0 on 2026-09-24, by diff rather than by re-running the chain.**
`apps/jsonrpc_server.py`, `io/jsonrpc.py` and `io/ws_server.py` are byte-identical to 1.10.0.
The 1.11.0 changes to `media_server.py`, `webrtc_utils.py` and `central_signaling_relay.py`
touch no DataChannel, JSON-RPC, binding or admission code, and the signalling server still
listens on `*:8443`. 1.11.0's "trusted HTTPS endpoints" work (#1365) is about where the daemon
sends its token, not about who may reach this surface. Nothing found says the answer changed.
The end-to-end probe was not repeated.

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
- ~~**Keep shipping `conversation.mic` / `backend.config` for now.**~~ **Superseded by D20**,
  which neutered both rather than keeping them: the mic is read-only and the backend target
  every writer is refused. The reasoning below was right that removal is the only in-app lever —
  it was wrong that the lever had to stay unpulled.
- ~~**The real fix is authenticating `/rpc`** — a per-instance bearer token from `.env`.~~
  **Superseded by D20, and this one was wrong rather than merely premature.** There is no
  channel that delivers a secret to the dashboard's iframe without delivering it equally to
  anyone else who can reach the port, and the SDK carries no credential to the app at all.
  The three verifications are under "Why there is no credential" below. The `Origin` check
  half of the idea survived and did land; the token half cannot be built. Anyone tempted to
  re-file it should read that section first.

- **Enrolment is deliberately absent from this surface, and that is a decision rather
  than an omission.** W28 added a flow that creates a learner and stores their
  faceprint — biometric data about somebody who lives in the house. Every writer on
  `/rpc` is refused outright by the policy recorded above, and `test_no_writer_is_
  reachable_over_the_network` enforces it, so putting enrolment here would mean
  overturning that policy for the most sensitive writer this app has. It is an
  operator command instead (`enrol`), which needs a shell on the machine the robot
  runs on. Nothing about enrolment is registered in `console.py`, and no change was
  needed to keep it that way: `test_the_exposed_method_set_is_exactly_what_was_signed_
  off` fails the moment any method is registered without being added to the signed-off
  set. Anyone tempted to add a "enrol from the dashboard" button should read the
  "Why there is no credential" section first — the iframe cannot be told apart from
  anyone else on the network.

## What was done about it (D20)

D15 had bound the app's own UI port to loopback to keep these methods off the household
LAN. That was wrong twice over, and the record is worth keeping because the reasoning is
the part that generalises.

**It broke the app on the deployment target.** The desktop dashboard discards the host in
`custom_app_url` — in all three places it opens an app it does `new URL(url); hostname =
LI()`, and `LI()` returns the robot's LAN address whenever the connection is over wifi —
so on a Wireless unit it loads `http://<robot-lan-ip>:7860/`, which a loopback-bound
uvicorn refuses. Its liveness hook HEAD-polls that same URL and calls `stopCurrentApp`
after 60s, so the tutor died about a minute after starting, on every unit. A Mac never
shows it, because a non-wifi connection makes `LI()` return `localhost`.

**And it did not close the exposure anyway**, because the daemon relay above reaches the
app on the loopback regardless.

### Why there is no credential

The obvious fix — authenticate `/rpc` — is not available to this app, verified three ways:

- The dashboard *does* preserve `custom_app_url`'s query string, but that value is a static
  literal the SDK regex-parses out of `main.py` **source**. A token there would be committed
  to the repository and identical in every household, which is not a secret.
- The app serves its own `index.html` on the same port, and that port must be LAN-reachable
  for the dashboard to work, so a token embedded in the page is readable by exactly the
  caller it would be meant to exclude.
- `reachy_mini/apps/jsonrpc_server.py` carries no token, secret or auth mechanism, and
  `_serve` calls `websocket.accept()` unconditionally.

So the protection could not be *who may call*; it had to be *what a call can do*.

### What changed

- **The bind is LAN-reachable again**, because the dashboard requires it and no address
  satisfies both it and the threat model.
- **The surface is an allow-list.** `/rpc` registers **21** methods, and the first attempt
  at this neutered the two the task happened to name — `conversation.mic` and
  `backend.config` — while leaving eight further writers reachable: `personalities.save`,
  `personalities.delete`, `personalities.apply`, `voices.apply`, `tool_spaces.add`,
  `tool_spaces.remove`, `profile_tools.save`, `profile_tools.reset`. `tool_spaces.add`
  installs a caller-named Hugging Face Space as a tool the conversation can call. Opening
  the port having closed two of ten doors was a net loss, and naming the dangerous ones is
  the same losing shape as D19's four-character deny-list and D11's substring rule. So a
  `JsonRpcServer` subclass now refuses every method **not** named in
  `_RPC_METHODS_EXPOSED_ON_THE_NETWORK`. `method()` delegates to `register()`, so the one
  override covers both the decorator used in `console.py` and the explicit
  `rpc.register(...)` calls in the three route modules — and a method added later is
  refused **by default** until somebody comes here and exposes it deliberately.
- **What stays exposed** is the read side — `conversation.status`, `personalities.list`,
  `.all`, `.load`, `.avatar`, `voices.list`, `voices.current`, `tool_spaces.list`,
  `profile_tools.get` — plus `conversation.mic` (which reports state and refuses a `muted`
  parameter rather than ignoring it), and `conversation.say` / `conversation.interrupt`.
  Those last two are **not** reads and are the accepted exposure here: they make the robot
  speak and cut it off, they persist nothing, and the dashboard's conversation view is what
  they are for. That is a decision, not an oversight.
- **An `Origin` check** on the handshake, added by registering the route rather than
  patching the SDK. It closes the cross-origin-browser route — a WebSocket handshake is
  exempt from the same-origin policy and is not preflighted. Comparing Origin to the Host
  header alone would not have been enough: a caller supplies both, so a page on
  `evil.example` rebound by DNS to the robot's address sends a matching pair and would be
  admitted. The check is therefore anchored on the fact that this server is only ever
  addressed by something that cannot be **rebound** — an IP literal, `localhost`, or an mDNS
  `.local` name, which has no public delegation. A public DNS name is refused. `.local` is
  admitted deliberately: the dashboard often uses the robot's mDNS name, and refusing it would
  have locked the dashboard out through this very fix. Note `.local` is not unspoofable —
  mDNS is unauthenticated — but the adversary who can spoof it is already on the link, where
  the Origin header is theirs to write and the real mitigation is that every writer is
  refused. This check buys the remote-browser case. An
  **absent** Origin stays allowed, because the daemon relay sends none and refusing it would
  break the daemon rather than an attacker.

### What this costs

Configuration moves to `.env` and the instance's files. The settings UI still shows state,
but it cannot save a personality, install a tool Space, change the voice, or repoint the
speech backend over the network. `HF_REALTIME_WS_URL` in `.env` is how a realtime server is
pointed at now, and that is the only way.

That is the real price of the position this app is in: the port must be open, no caller on
it can be told apart from any other, and so the only honest lever is what a call is allowed
to do. Anything cheaper is a guess about who is calling.
