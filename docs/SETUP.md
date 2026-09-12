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
> It has no camera, which is why the app is run with `--no-camera` below.

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
- `--no-camera` because the simulator has no camera
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

Diagnosed in D22 on 2026-09-12, and **the honest summary is that the fault was located but not
explained.** What follows separates what was measured from what was inferred, because an
earlier draft of this entry stated a mechanism nobody had measured and the next person would
have inherited it as fact.

**What was measured**, all read-only, all against the daemon that was failing (PID 4545,
started 07:43, `--desktop-app-daemon --mockup-sim`):

1. **The camera IPC socket delivers nothing.** The daemon publishes captured frames to a unix
   socket at `/tmp/reachymini_camera_socket` (`CAMERA_SOCKET_PATH` in `daemon/utils.py`, fed by
   `media/media_server.py`). Attaching a `unixfdsrc` consumer gave `state: paused` and
   **0 frames in 6 seconds**, with no error and no warning on the bus. Command below.
2. **The signalling server accepts TCP and then says nothing.** Two clients — `websockets` and a
   hand-rolled socket doing the HTTP upgrade — both connected to `ws://localhost:8443` and
   received **zero bytes**, then timed out.
3. **The same daemon was answering on 8443 at its own boot.** Its log shows eight WebSocket
   connections between `11:43:50` and `11:44:05.189` UTC, each completing and registering —
   seven as `[Listener]` and one as **`[Producer]` at 11:43:54.679**. So something registered as
   a producer that morning, and the server was replying then, which it is not now. Note the
   limit of that: the log shows a producer *registering*, not a single video frame flowing. It
   is evidence the process was answering, not proof the camera was ever delivering today.
4. **macOS still sees the camera.** `system_profiler SPCameraDataType` lists `USB Camera
   VID:1133 PID:2085` — the only video device on this machine, which has no built-in FaceTime
   camera — and GStreamer's device monitor enumerates it with full caps. Enumerating is not
   delivering.
5. **Corroboration, with a caveat that matters.** `autovideosrc ! videoconvert ! fakesink` driven
   to PLAYING in the daemon's own interpreter returns `GST_STATE_CHANGE_ASYNC`, stuck at PAUSED
   with PLAYING pending, silently. Treat this as weak: it opens the camera itself and runs under
   the *terminal's* macOS camera (TCC) identity, not the daemon's, so a terminal-side permission
   denial would produce the same stall for a reason that says nothing about the daemon. It was
   not ruled out — `TCC.db` is unreadable under SIP.

**What that establishes, and what it does not.** Two independent things in one process are both
stalled and both silent: frame delivery on the IPC socket, and the signalling server's replies.
Measurement 1 is the load-bearing one, because a socket consumer reads frames the daemon has
already published rather than opening the device — so it is not confounded by the daemon holding
the camera exclusively, nor by the prober's own camera permission. What is **not** established is
why, or whether the two stalls share a cause. In particular, do **not** read the zero-byte 8443
result as "there is no producer to announce": that server sends its `Welcome` on connect
regardless of producers, and the log records the Central Relay confirming exactly that for the
11:43:50 connection — "Local WebRTC connection verified (welcome received)" — four seconds
before any peer registered as a producer. (That confirmation appears for the 11:43:50 peer
only; the 11:43:53 one shows a registration but no logged welcome, so do not count it.) A
connection receiving nothing at all is therefore a stall in the signalling server itself, not a
consequence of having no video.

The SDK runs that server on a single GLib mainloop thread (`ThreadId(01)` throughout the log),
which is a plausible way for one wedged component to take the other down — but that is a
**hypothesis nobody measured**, recorded here so the next person can test it rather than believe
it. Likewise unmeasured: whether the IPC branch's own failure modes (`media_server.py`'s
`unixfdsink` handling) could account for measurement 1 without capture being at fault.

**Nothing in this repository is on either path.** The failing components are the desktop app's
managed daemon and its GStreamer stack; the app under development here was not even running.

**The decisive test — read-only, six seconds, and development-Mac only.** Ask the socket for
frames rather than guessing from the dashboard. Do **not** carry this habit onto a deployed
robot: the same socket on a Wireless unit carries live household video, and the distance between
this frame counter and a recorder is one token (`fakesink dump=True`, or a `filesink` in place
of it). Here it counts buffers and reads no pixels.

```bash
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

Frames arriving means delivery is healthy and the problem is further along. Zero frames with
`state: paused` and a silent bus is what it said on 2026-09-12.

**What to try, in order. None of these was verified to fix it** — the cause sits below anything
this repository owns, and restarting a daemon out from under someone using it was out of scope.

1. Quit and reopen Reachy Mini Control, since the process was healthy at its own boot and
   degraded in place.
2. Check System Settings → Privacy & Security → Camera for Reachy Mini Control. A denial there
   is a candidate consistent with a silent never-delivering session; it was not confirmed, and
   the 24-hour system log showed no denial line (which is weak evidence — a headless process can
   hang rather than log).
3. Unplug and replug the webcam. It is the only video device on this machine, so a USB camera in
   a bad state leaves the daemon with nothing to capture.

**Negative results, recorded so nobody re-checks them.**

- **An agent session did not cause this.** The one time an agent touched port 8443 it was a
  read-only WebSocket probe against daemon PID 6670. That process no longer exists: the daemon
  that failed is PID 4545, started fresh the next morning, and its own log shows it handshaking
  correctly — including registering a `[Producer]` — before degrading. No SDK file was modified.
- **`--mockup-sim` is supposed to have a camera.** It maps to `SimulationMode.MOCKUP`, which
  builds an `autovideosrc` capture chain; `daemon/daemon.py` says mockup-sim "behaves exactly
  like a real robot for apps (they open webcam directly)". "The simulation has no camera" is true
  of standalone `no_media` SDK scripts and not of this path.
- **No version drift between the two interpreters that were compared.** GStreamer core is 1.28.7
  in both the daemon venv and `reachy_mini_env`, so an earlier note about a 1.28.3 / 1.28.7
  discrepancy did not reproduce, and the venv symlinks predate the session that was suspected.
  Narrower than "not a version change": `apps_venv` was never compared, and the SDK version rests
  on a dist-info folder name with no earlier snapshot to diff against.
- **Port 8443 is not closed and nothing is refusing the connection.** It is listening on PID 4545
  and the TCP handshake completes; the silence is afterwards.

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
