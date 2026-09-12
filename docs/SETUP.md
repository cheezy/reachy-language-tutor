# Development environment setup

How to get the Reachy Mini language tutor running on a Mac, from nothing.

Budget about 30–45 minutes, most of it waiting on downloads. You do **not** need a physical
robot — everything here works against the simulator.

Read `../CLAUDE.md` for the project's architecture decisions and `plan.md` for background
before you start writing code.

---

## The one thing that will bite you

**The SDK in your virtualenv and the robot service (the "daemon") must be the same version.**

They live in two different places, they are upgraded by two different mechanisms, and when
they drift apart you do not get a clean error. The app connects successfully, prints nothing
alarming, and then every motion command fails with:

```
ConnectionError: Lost connection with the server.
```

If you read nothing else here, read [Keeping the SDK and daemon in step](#5-keeping-the-sdk-and-daemon-in-step).

---

## 1. Prerequisites

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
brew install git git-lfs
git lfs install
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen your terminal so `uv` is on your `PATH`.

Python **3.10–3.12** is required; 3.12 is what we develop against.

```bash
uv python install 3.12 --default
```

## 2. Reachy Mini Control (the desktop app)

Download and install it from the
[Reachy Mini documentation](https://huggingface.co/docs/reachy_mini). Launch it and start the
robot in **simulation** mode.

This app is not just a GUI. It runs the **robot service** that everything else talks to, on
`http://localhost:8000`. It must be running whenever you are developing.

Confirm it is up:

```bash
curl -s localhost:8000/api/daemon/status | python3 -m json.tool
```

You want `"state": "running"`. Note the `"version"` — you will need it in step 5.

> The lightweight **mockup-sim** (`"mockup_sim_enabled": true`) is what we use day to day.
> A **standalone SDK script** gets no camera there, which is why the app is run with
> `--no-camera` below. That is not the same as "mockup-sim has no camera": the **daemon** does
> open the Mac's webcam in this mode. The loose phrasing is what sent D22 down the wrong path —
> see the Troubleshooting entry on the 8443 timeout, where the camera turns out to work and
> something else is broken.

## 3. The project

```bash
git clone <REPOSITORY-URL> ~/dev/reachy/learn_language
cd ~/dev/reachy/learn_language
```

> **This URL does not exist yet.** At time of writing the project has no remote and no initial
> commit — see [Repository layout](#repository-layout-unresolved) at the end. Until that is
> settled, get the directory from whoever is working on it.

## 4. The development virtualenv

```bash
uv venv ~/dev/reachy/reachy_mini_env --python 3.12
~/dev/reachy/reachy_mini_env/bin/pip install "reachy-mini[mujoco]"
```

We deliberately **do not** activate the venv in these instructions; every command names its
interpreter explicitly. That is not pedantry — the whole class of bugs in this document comes
from running the wrong Python, and explicit paths make that impossible.

Now install the app itself, in editable mode:

```bash
cd ~/dev/reachy/learn_language/reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/pip install -e .
```

> ⚠️ **This step can silently change your SDK version.** The app declares
> `reachy-mini>=1.10.0rc5`, so pip will upgrade `reachy-mini` to satisfy it — which may pull
> it out of step with the daemon. Always run step 5 immediately after this.

## 5. Keeping the SDK and daemon in step

There are **two** `reachy_mini` installations on your machine, and they must agree:

| | Where | Upgraded by |
|---|---|---|
| **The SDK** | `~/dev/reachy/reachy_mini_env` | you, with `pip` |
| **The daemon** | `~/Library/Application Support/com.pollen-robotics.reachy-mini/.venv` | the desktop app, when it provisions |

Check both:

```bash
~/dev/reachy/reachy_mini_env/bin/python -c 'import reachy_mini; print(reachy_mini.__version__)'
curl -s localhost:8000/api/daemon/status | python3 -c 'import sys,json; print(json.load(sys.stdin)["version"])'
```

**If the numbers differ**, upgrade the daemon's venv to match your SDK. The desktop app ships
its own `uv`, so use that:

```bash
APP_SUPPORT=~/Library/"Application Support"/com.pollen-robotics.reachy-mini
"$APP_SUPPORT/uv" pip install --python "$APP_SUPPORT/.venv/bin/python3" "reachy-mini==<YOUR-SDK-VERSION>"
```

Then restart the daemon by relaunching the desktop app:

```bash
open -a "Reachy Mini Control"
```

Give it ~15 seconds and re-check the versions.

> **Do not use `POST /api/daemon/restart`.** It kills the control app along with the daemon and
> nothing comes back on its own. Relaunching the app is the reliable path.

### Why this keeps happening

Restarting or reinstalling the desktop app does **not** pick up a newer SDK — the version is
provisioned into that managed venv and stays there. Reachy Mini Control 0.9.34, for instance,
provisions `reachy_mini 1.8.0`, which is older than this project's template requires.

So a desktop app update can silently put you back into version skew. **If motion starts
failing after an app update, check the versions first.** It is almost always this.

## 6. Developer tooling and tests

The project uses **ruff** (formatter and linter) and **pytest** (tests). They are declared in
the app's `pyproject.toml` but are not installed by the steps above:

```bash
~/dev/reachy/reachy_mini_env/bin/pip install pytest pytest-asyncio "ruff==0.15.20"
```

Run the suite — 223 tests, about 4 seconds:

```bash
cd ~/dev/reachy/learn_language/reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/python -m pytest -q
```

Format and lint your changes before finishing a task:

```bash
~/dev/reachy/reachy_mini_env/bin/ruff format src    # rewrites files
~/dev/reachy/reachy_mini_env/bin/ruff check src     # reports problems
```

These same three commands are the Stride `after_doing` hook, so a task cannot be completed
while any of them fails. See `.stride.md`.

> **The suite reports 30 skips, and that is expected.** Seven files come from the upstream
> conversation app and test profile *switching*, which this app deliberately disables via
> `config.LOCKED_PROFILE`. Those 30 tests are skipped individually by a `skipif` bound to that
> constant (`reachy_language_tutor/tests/profile_lock.py`), so they resume on their own if the
> app ever stops locking its profile. A 31st test fails for an unrelated reason — a packaged
> path three characters over the Windows wheel budget — and pins that one known violation
> rather than being marked, so any further violation fails the suite. See D23.
>
> They used to be excluded a whole file at a time, which also threw away 69 passing tests and
> silently swallowed anything added to those files afterwards. D21 replaced that with the
> per-test marks; `tests/test_pytest_configuration.py` now fails if any test file on disk
> stops being collected.

## 7. Hugging Face login

The voice backend authenticates as you. Without a token it will not start.

```bash
~/dev/reachy/reachy_mini_env/bin/hf auth login
```

No API keys are needed beyond this. The app uses the Hugging Face realtime backend, which is
free and requires no separate provider account.

## 8. Verify everything

```bash
cd ~/dev/reachy/learn_language
./scripts/check-env.sh
```

This checks every failure mode described in this document — host tools, both Python
environments, version skew, the daemon, the profile format, and your Hugging Face token — and
prints a concrete fix for anything broken. It exits non-zero on failure, so it works in CI too.

Re-run it any time something stops working. It is faster than debugging by hand, and it knows
about the traps.

## 9. Run the app

```bash
cd ~/dev/reachy/learn_language/reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main --ui --no-camera
```

- Web UI: **http://127.0.0.1:7860/**
- `--no-camera` because a **standalone SDK script** gets no camera in simulation — not because
  mockup-sim lacks one. The daemon there does open the Mac's webcam, and as of D24 deadlocks
  while serving it (Troubleshooting, the 8443 timeout)
- `--debug` for verbose logging, and the only way to get the conversation's actual
  words into the log. Without it the log records that a turn happened and how long
  it was (`role=user content=str(len=32)`) but not what was said, so a log captured
  from someone's home does not carry their side of the conversation.

Within a few seconds Reachy should greet you through your Mac's speakers and ask which
language you want to practise. Talk to it using your Mac's microphone.

> Any guide telling you to use `--gradio` on port **7861** is out of date — that comes from a
> stale message printed by the app-assistant CLI. The flag is `--ui` and the port is **7860**.

---

## Expected noise

These appear on every simulator run and are **not** problems:

```
ERROR ... No Reachy Mini Audio USB device found!
WARNING ... ReSpeaker device not found.
WARNING ... Reachy audio startup config was not applied.
```

The simulator has no Reachy audio hardware, so audio falls back to your Mac's default
microphone and speakers, with echo cancellation done in software by GStreamer. This is fine.

---

## Troubleshooting

### Motion fails with `ConnectionError: Lost connection with the server.`

Version skew. See [step 5](#5-keeping-the-sdk-and-daemon-in-step). The SDK also prints a
`RuntimeWarning` naming both versions — worth scrolling up for.

### The app exits immediately at startup

Look for:

```
CRITICAL LOCKED_PROFILE '_reachy_language_tutor_locked_profile' has no profile definition
```

The app is locked to a single profile and calls `sys.exit(1)` if it cannot find it. It needs:

```
reachy_language_tutor/profiles/_reachy_language_tutor_locked_profile/profile.md
```

**Profile format.** Profiles are a single `profile.md`: a TOML front-matter block delimited by
`+++` declaring `schema_version` and `default_tools`, followed by the system prompt as Markdown.
Copy `reachy_language_tutor/profiles/chess_coach/profile.md` as a minimal example.

An older layout used separate `instructions.txt` and `tools.txt` files. **It is no longer
read.** If you find those — particularly under `src/reachy_language_tutor/profiles/` — they are
dead files from a stale generator; delete them.

### Reachy speaks, but never responds when you speak to it

The single most likely cause is **microphone permission**, and it is invisible: macOS hands a
denied process **silence** rather than an error, so the app records nothing, logs nothing
unusual, and simply never replies.

**System Settings → Privacy & Security → Microphone → enable your terminal application.**

The app inherits the permission of whatever launched it, so it is the *terminal* that needs
access, not Python. If a permission prompt appeared once and was dismissed, macOS records that
as a denial and never asks again.

Two other causes, in order of likelihood:

- **The wrong input device is default.** The app logs `No specific audio card found, using
  default audio source` and takes whatever macOS says is default — which may be a monitor, a
  webcam, or a virtual device, not the microphone you are speaking into. Set the right one in
  **System Settings → Sound → Input**. GStreamer picks the device when it builds its pipeline,
  so **restart the app** after changing it; switching while it runs has no effect.
- **The microphone is muted or turned down** at the OS level.

`./scripts/check-env.sh` now measures the actual capture level and reports the device, so run
that first. A peak near **-350 dB is digital silence**, not a quiet room; normal room noise sits
around -30 to -50 dB.

**How to confirm from the app's log**, without guessing whether it heard you:

```bash
grep -icE "role=user|transcript|speech_started" <logfile>   # inbound speech events
grep -oE "after [0-9.]+s idle" <logfile> | tail -3          # idle timer
```

Zero speech events plus an idle timer that keeps climbing and never resets means no audio is
arriving at all — the app is deaf, not confused.

### The robot stops moving and the log fills with connection errors

```
ERROR ... Failed to set robot target: Lost connection with the server. (suppressed 52 repeats)
```

The daemon was restarted underneath a running app — most often by relaunching Reachy Mini
Control. The app survives but can no longer drive the robot, which looks like the robot has
fallen asleep. **Restart the app**, not the desktop app. Note the ordering generally: start the
desktop app first, the conversation app second.

### No sound, or the app never speaks

Check your Hugging Face token (`./scripts/check-env.sh` covers this). Then check macOS
microphone permissions for your terminal under System Settings → Privacy & Security.

### Port 8000 or 7860 already in use

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Only one app may drive the robot at a time. Quitting the other one is usually the fix.

---

### The dashboard camera says "Timed out waiting for WebRTC stream from ws://localhost:8443"

**Answered in D24 on 2026-09-12. This is a deadlock inside GStreamer's `webrtcsink`
(`gst-plugin-webrtc` 0.15.3), it reproduces on every daemon start, and nothing in this
repository is on the failing path or can fix it.** D22 located the fault and left three
candidate recoveries untested; D24 tested them and found the mechanism. **None of the three
recoveries works**, and the reason none of them could is that the camera was never the problem.

**The short version.** About ten seconds after the daemon starts, the first WebRTC session
deadlocks four threads inside `libgstrswebrtc`. The camera is *fine and still capturing*: its
frames pile up behind the `tee` that feeds both the WebRTC branch and the camera IPC branch.
A `tee` pushes to its branches serially, so the jammed WebRTC branch head-of-line blocks the
capture thread and starves the IPC branch as well. One deadlock, both symptoms — no frames on
`/tmp/reachymini_camera_socket`, and no reply on 8443.

That answers D22's open question. Its "single GLib mainloop" idea was right that the two stalls
**share a cause**, and wrong about which one: the shared resource is a `std::sync::Mutex` in
`BaseWebRTCSink`, not a mainloop.

#### What was measured (D24)

All read-only. Nothing captured, stored or transmitted an image; the probe counts buffers.

1. **The fault reproduced first, unchanged.** Against the daemon D22 diagnosed (PID 4545, then
   five hours old): the socket probe below gave `state: paused` and **0 frames in 6 s**, and a
   WebSocket upgrade to `ws://localhost:8443` returned **0 bytes** before timing out.
2. **The process was not wedged as a whole.** Its Python asyncio side was healthy the entire
   time — `central_signaling_relay` logged a `setPeerStatus` every 10 s, and `webrtc_utils`
   retried TURN every ~30 s, right up to the moment of the test. Only some components stopped:

   | Component | Last logged | Still alive? |
   |---|---|---|
   | `gst_plugin_webrtc_signalling::server` (Rust) | 11:44:05 | no |
   | `reachy_mini.media.media_server` | 11:50:49 | no (event-driven; see caveat) |
   | `reachy_mini.media.webrtc_utils` | 16:43:19 | **yes** |
   | `reachy_mini.media.central_signaling_relay` | 16:43:31 | **yes** |
   | `uvicorn.access` | 16:39:48 | **yes** |

   The caveat on `media_server`: it logs on events, so silence alone is not proof it stalled.
   The signalling server is different — a connection **arrived** at 16:41 and it logged nothing
   about it and answered nothing, so its accept path really is dead, not merely idle.
3. **The stack sample is the decisive measurement**, and it is what turns all of the above from
   symptoms into a mechanism. `sample 4545 3` showed four threads blocked in **100 % of samples**
   (1570/1570), in a cycle:

   | Thread | Where it is blocked | What it is waiting for |
   |---|---|---|
   | `webrtcbin-…:pc` | `BaseWebRTCSink::on_remote_description_set` → `SessionInner::connect_input_stream` → `gst_bin_sync_children_states` → `gst_element_set_state_func` | a GStreamer element state lock — **while holding the session `Mutex`** |
   | `queue_webrtc:src` | rswebrtc pad chain fn → `Mutex::lock` | that same session `Mutex` |
   | `tokio-rt-worker` | `Signaller::connect` → `emit_by_name` → `BaseWebRTCSink::start_session` → `Mutex::lock` | that same session `Mutex` |
   | `tokio-rt-worker` | `gst_element_set_state_func` → `gst_webrtc_bin_change_state` → `g_cond_wait` | a state change `webrtcbin` can no longer complete |

   And the two threads that carry the visible symptoms:

   | Thread | Where it is blocked | What that proves |
   |---|---|---|
   | `autovideosrc1-actual-src-avfvide:src` | `gst_base_src_loop` → … → `gst_tee_chain` → `gst_queue_chain_buffer_or_list` → `g_cond_wait` | **the camera is working.** It is blocked *delivering* a captured frame into a **full** queue |
   | `queue_ipc:src` | `gst_queue_loop` → `g_cond_wait` | the IPC queue is **empty** — starved, which is why `unixfdsrc` sees nothing |

4. **It is a deadlock, not a slow path.** A second sample three minutes later showed all four
   threads at the same leaves, 1959/1959.
5. **It reproduces on a fresh process.** After quitting and reopening the app, the new daemon
   (PID 27910) was sampled ~90 s after boot and showed the **identical** four-thread cycle,
   1879/1879, with a different session UUID. Both of today's boots wedged the same way: 16 s
   after the 11:43 boot (after five short-lived sessions) and 11 s after the 16:47 boot (after
   one). Timeline of the second: daemon up `16:47:27`; a `[Listener]` registers `16:47:28`; the
   `[Producer]` registers `16:47:30`; a session starts and the SDP exchange runs at `16:47:32`;
   last signalling line ever, `16:47:38`.

**What this establishes, and what it does not.** The deadlock is established — four threads,
two processes, 100 % of samples, a named cycle. That the camera hardware and the app's macOS
camera permission are both *working* is established too, and by the strongest available
evidence: the capture thread is inside a push call, which it can only reach by having produced
a frame, and the queue it is pushing into is full, which takes many. What is **not** established
is the exact lock-order inversion in upstream's source — the cycle above is read off symbols,
and `libgstrswebrtc` was not read. Nor was it tested whether the camera IPC socket delivers in
the ten-second window before the first session starts; it plausibly does, but nobody measured it.

#### The recoveries D22 listed, and what each one actually did

1. **Quit and reopen Reachy Mini Control — tried, did not work.** Done at 16:47. The camera
   socket still reported 0 frames and 8443 still returned 0 bytes, and the fresh daemon was
   sampled in the same deadlock. This is now the *expected* result rather than a surprise: a
   restart cannot help, because the bug is reached from a clean boot every time.
2. **Check System Settings → Privacy & Security → Camera — not needed, refuted as a cause.**
   Measurement 3 shows the capture thread holding a captured frame and a full downstream queue.
   A denied camera does not produce frames. The permission is granted and working.
3. **Unplug and replug the webcam — not performed, and refuted as a cause by the same evidence.**
   It needs hands at the machine and there is nothing for it to fix; the device is delivering.

**So: the camera and 8443 did not clear, and they did not clear *together* — neither one
cleared.** The shared-cause question is still answered, just not by the recovery: it is answered
by the sample, which shows both symptoms hanging off the one `Mutex`.

#### The diagnostic commands

**The six-second socket probe — read-only, and development-Mac only.** It asks the socket for
frames rather than guessing from the dashboard. Do **not** carry this habit onto a deployed
robot: the same socket on a Wireless unit carries live household video, and the distance between
this frame counter and a recorder is one token (`fakesink dump=True`, or a `filesink` in place
of it). Here it counts buffers and reads no pixels.

```bash
# Development Mac only. On a Wireless unit this socket carries live household video.
# This pipeline counts buffers and reads no pixels -- keep it that way.
DV="$HOME/Library/Application Support/com.pollen-robotics.reachy-mini/.venv/bin/python3"
"$DV" - <<'PY'
import gi; gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib
Gst.init(None)
p = Gst.Pipeline.new("probe")
src = Gst.ElementFactory.make("unixfdsrc"); src.set_property("socket-path", "/tmp/reachymini_camera_socket")
sink = Gst.ElementFactory.make("fakesink"); sink.set_property("sync", False); sink.set_property("signal-handoffs", True)
n = {"f": 0}; sink.connect("handoff", lambda *a: n.__setitem__("f", n["f"] + 1))
for e in (src, sink): p.add(e)
src.link(sink); p.set_state(Gst.State.PLAYING)
print("state:", p.get_state(8 * Gst.SECOND)[1].value_nick)
end = GLib.get_monotonic_time() + 6_000_000
while GLib.get_monotonic_time() < end: GLib.MainContext.default().iteration(False)
print("frames in 6s:", n["f"]); p.set_state(Gst.State.NULL)
PY
```

**The stack sample — this is the one that answers the question.** Read-only, three seconds, no
privileges beyond your own process:

```bash
# Write it to a private directory, not a fixed name in shared /tmp.
OUT="$(mktemp -d)/daemon-sample.txt"
sample "$(pgrep -f 'reachy_mini.daemon.app.main' | tail -1)" 3 -file "$OUT"
grep -A4 'Thread.*queue_webrtc:src' "$OUT"   # Mutex::lock  => this bug
grep -A4 'Thread.*avfvide' "$OUT"            # tee/queue    => camera is fine
rm -f "$OUT"                                 # see the warning below before keeping it
```

**Never attach that file to an upstream issue.** `sample` output is not only the thread stacks
you grepped: it embeds the sampled process's full executable path, its command line, and a
Binary Images section listing the absolute path of every loaded dylib — so on this machine it
contains literal `/Users/<your-username>/Library/Application Support/…` strings. Sending the raw
file to a third party would ship your username and home layout with it. Send the grepped thread
lines and the version list below, and nothing else. That is why the command writes to a
`mktemp -d` directory and deletes it: a fixed, predictable name in a world-readable `/tmp` is
also readable by any other local account.

`queue_webrtc:src` sitting in `Mutex::lock` inside `libgstrswebrtc` is the signature. If you see
it, stop investigating the camera.

#### The upstream report

Affected versions, all confirmed on this machine in this session:

- `reachy-mini` 1.10.0, daemon started `--desktop-app-daemon --mockup-sim`
- `gst-plugin-webrtc` (`rswebrtc`) **0.15.3-e92296285** — `webrtcsink`
- GStreamer core, `webrtcbin`, `unixfd`, `applemedia` — all **1.28.7**
- macOS 26.6.1 (25G76), arm64

The report to `pollen-robotics/reachy_mini` is: **`webrtcsink` deadlocks during the first
session's `on_remote_description_set`, and because the daemon puts the camera IPC branch behind
the same `tee` as the WebRTC branch, the deadlock also kills the local camera socket.** Include
the two thread tables above and the version list; there is no exploit and nothing to weaponise.
Two things are worth saying to upstream beyond the bug itself, because they are what turned one
broken feature into two:

- The cycle is between `SessionInner`'s `Mutex` and GStreamer element state locks, reached from
  three directions at once (`connect_input_stream`, the pad chain function, and `start_session`
  via the signalling callback).
- `media_server.py` links the IPC branch and the WebRTC branch to one `tee` with a plain
  `queue` on each. A `leaky=downstream` queue on the WebRTC branch would have contained the
  damage to WebRTC instead of taking the camera socket down with it. That is a remark for
  upstream, **not a change to make here** — see below.

#### Nothing in this repository changes because of this

The failing components are the desktop app's managed daemon and its GStreamer stack. The app
under development here was not even running. Do not add a workaround to this repository for an
environmental fault in a dependency — that was D22's finding and D24 confirms it at the level of
mechanism.

**What it costs us.** Milestone 4 (face recognition) wants camera frames. Until this is fixed
upstream, the daemon's camera path on this Mac is unusable: `/tmp/reachymini_camera_socket` is
not a source you can build on, and neither is the daemon's own face detection. The
`face_target` block on `/api/daemon/status` still answers, but three polls in this state all
returned `{"detected": false, "x": null, "y": null, "roll": null, "ts": null}` — a null `ts`, so
nothing has ever been detected. (Nobody was deliberately in frame, so that is consistent with
starvation rather than proof of it.) Prototype against a camera directly instead.

Which camera is worth knowing, because D22 recorded only one and there are **two**. Measured in
D24 with `system_profiler SPCameraDataType` and GStreamer's device monitor, which agree:

| Device | Notes |
|---|---|
| `USB Camera VID:1133 PID:2085` | the wired webcam; this is the one the daemon opens |
| `iPhone Camera` (`iPhone17,3`) | Continuity Camera — present only when the phone is nearby and willing |

This machine has **no built-in FaceTime camera**, so those two are the whole inventory. Whether
the daemon holds the USB webcam *exclusively* while it runs was **not** tested, so do not assume
you can open it alongside the daemon — try it, and fall back to stopping the daemon first.

#### Negative results, recorded so nobody re-checks them

- **An agent session did not cause this.** D22 established it (the probed daemon, PID 6670, no
  longer exists; no SDK file was modified) and D24 settles it: the bug reproduces from a clean
  boot with nothing attached but the desktop app's own dashboard.
- **`--mockup-sim` is supposed to have a camera.** It maps to `SimulationMode.MOCKUP`, which
  builds an `autovideosrc` capture chain; `daemon/daemon.py` says mockup-sim "behaves exactly
  like a real robot for apps (they open webcam directly)". "The simulation has no camera" is true
  of standalone `no_media` SDK scripts and not of this path.
- **No version drift between the two interpreters that were compared.** GStreamer core is 1.28.7
  in both the daemon venv and `reachy_mini_env`. Narrower than "not a version change":
  `apps_venv` was never compared.
- **Port 8443 is not closed and nothing is refusing the connection.** It listens and the TCP
  handshake completes; the silence is afterwards, and measurement 3 says why — the tokio worker
  that would answer is blocked on the same `Mutex`.
- **Do not read the zero-byte 8443 result as "there is no producer to announce".** That server
  sends its `Welcome` on connect regardless of producers. The silence is a blocked accept path.
- **The TURN warnings in the log are a red herring.** `Failed to fetch TURN credentials` for
  `turn.fastrtc.org` repeats every ~30 s because DNS does not resolve it here. The dashboard is
  a loopback peer and needs host candidates only; the daemon logs `No TURN servers held;
  offering host/srflx only` and proceeds to a full SDP exchange.

## Creating a new app from the template (rarely needed)

You should not need this — the app already exists. It matters only if you scaffold a second
app. **Never create app folders by hand**; always use the assistant:

```bash
~/dev/reachy/reachy_mini_env/bin/reachy-mini-app-assistant create \
    --template conversation <app_name> <path>
```

Do **not** pass `--publish` unless the project owner has asked for it, even though the official
docs tell you to always use it.

Two known problems with the assistant, both from version skew between the SDK and the
conversation-app template:

1. **Older SDKs (1.8.x) clone a branch that no longer exists**, failing with
   `fatal: Remote branch develop not found in upstream origin`. Upstream renamed it to `main`.
   Fixed in SDK 1.10.0.
2. **The assistant may generate the obsolete profile layout** (`instructions.txt` + `tools.txt`
   under `src/<app>/profiles/`) that the current app cannot read. The generated app will exit
   at startup. Write a `profile.md` in the top-level `profiles/` directory instead, and delete
   the generated one. See [above](#the-app-exits-immediately-at-startup).

Validate any scaffolded app with:

```bash
~/dev/reachy/reachy_mini_env/bin/reachy-mini-app-assistant check .
```

---

## Things worth knowing before you change anything

- **One voice backend.** This version of the conversation template dropped multi-backend
  support. Hugging Face realtime is the only option; `BACKEND_PROVIDER` and `MODEL_NAME` are
  warned about and ignored. `plan.md` §6 predates this and still describes choosing between
  OpenAI Realtime and Gemini Live.
- **Secrets go in `reachy_language_tutor/.env`**, which is gitignored. Never commit it. Nothing
  is required there for current milestones — the Hugging Face CLI login covers it.
- **The database is the source of truth for learner progress**, not the LLM. Its schema, the
  seeded sample data, and how "next lesson" is decided are documented in
  [`learner-database.md`](learner-database.md). And tools must
  take the learner's identity from app state, never from a tool argument, so nobody can talk
  their way into someone else's profile. See `../CLAUDE.md`.
- **Keep on-robot work light.** The deployment target is the Wireless model, whose onboard
  computer is much weaker than your Mac. The simulator will not reveal performance problems.

---

## Repository layout

```
learn_language/              <- the git repository (branch main)
├── CLAUDE.md                architecture decisions
├── .gitignore
├── docs/                    plan.md, SETUP.md, learner-database.md
├── scripts/                 check-env.sh
└── reachy_language_tutor/   the app; also the published Hugging Face Space
```

The project, not the app, is the repository root. The app directory doubles as the
published Space, so anything placed inside it ships to every robot — planning notes and
developer tooling stay above it. Publishing does not require the app to be its own
repository: the app assistant falls back to a Hugging Face API folder upload.

There is no remote yet. That is why `.stride.md`'s `before_doing` hook does not pull and
its `after_goal` hook does not publish — both would fail and block. Add those commands
once a remote exists.
