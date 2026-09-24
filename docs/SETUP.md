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

**An update also removes the local camera patch.** `scripts/patch_daemon_camera_leak.py`
edits `media_server.py` inside the daemon's `.venv` (see the 8443 entry under
Troubleshooting), and upgrading `reachy-mini` there replaces that file. It does *not*
remove the script's `media_server.py.orig` backup, because pip only deletes files it
installed: the 1.10.0 → 1.11.0 upgrade left the 1.10.0 file sitting beside the 1.11.0 one
as its "original". The script now re-takes the backup on every patch and refuses to
`--revert` from a backup that is not this install's own unpatched file, so re-run it after
an update and check with `--check`:

```bash
python3 scripts/patch_daemon_camera_leak.py --check   # UNPATCHED after an update
python3 scripts/patch_daemon_camera_leak.py           # then relaunch the desktop app
```

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
> app ever stops locking its profile. **Nothing else is skipped, marked or failing.** A 31st
> test used to fail over a packaged path three characters above the Windows wheel budget, and
> pinned that one violation rather than being marked. D23 re-derived the budget, found it had
> been miscounted by 7 characters — the path was inside it all along — and removed the pin.
> See D23 and the derivation comment in `tests/test_profile_paths.py`.
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

## 8b. Install it into the simulator's app list (optional)

Section 9 runs the app directly, which is the fast loop for development. To have it
appear in the Reachy Mini Control dashboard and start like any other app instead:

```bash
./scripts/install-into-simulator.sh          # --start to launch it too
./scripts/remove-from-simulator.sh           # the undo
```

Both are idempotent and verify by asking rather than by trusting the installer: the
install checks that a `reachy_mini_apps` entry point appeared and that the daemon lists
the app; the remove checks that both are gone.

**Which environment, because it is not the obvious one.** Apps live in the desktop app's
`apps_venv`, a third environment beside your SDK at `~/dev/reachy/reachy_mini_env` and
the daemon's own `.venv`. The script installs there with the desktop app's own bundled
`uv`, and prints `reachy_mini`'s version before and after — this app declares
`reachy-mini>=1.10.0rc5`, so an install *can* move the SDK and put it out of step with
the daemon, which is the trap section 5 is about. It warns if that happens.

**Not through `/api/apps/install`.** The daemon has that endpoint and it refuses a local
directory — `source_kind 'local' is not installable via the API`. It is for Hugging Face
spaces. Established by trying it, so nobody has to try it again.

The install is editable, so the simulator runs your working tree. Reinstall only after
changing entry points or dependencies.

## 9. Run the app

```bash
cd ~/dev/reachy/learn_language/reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main --ui --no-camera
```

- Web UI: **http://127.0.0.1:7860/**
- `--no-camera` because a **standalone SDK script** gets no camera in simulation — not because
  mockup-sim lacks one. The daemon there does open the Mac's webcam. D24 recorded that path as
  deadlocking while serving it; W27 measured it working, and found the deadlock is conditional
  rather than permanent (Troubleshooting, the 8443 timeout)
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
CRITICAL LOCKED_PROFILE '_reachy_language_tutor_locked' has no profile definition
```

The app is locked to a single profile and calls `sys.exit(1)` if it cannot find it. It needs:

```
reachy_language_tutor/profiles/_reachy_language_tutor_locked/profile.md
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

> **Update, 2026-09-24, on `reachy-mini` 1.11.0: four boots, no deadlock.** Upstream
> issue #1416 is still open, the 1.11.0 release notes claim no fix for it, and
> `rswebrtc` (0.15.3-e92296285) and GStreamer (1.28.7) are the same builds the entry
> below was measured on. The Mac media pipeline is unchanged too; 1.11.0's
> `media_server.py` changes are the Raspberry Pi encoder path and TURN error handling.
> Even so, each of these boots completed its first WebRTC session and stayed healthy,
> measured with the diagnostic commands below (plus a WebSocket read of 8443):
>
> | Boot | Local patch | Measured at | `queue_webrtc:src` | IPC frames in 6 s | 8443 |
> |---|---|---|---|---|---|
> | first boot after the upgrade | none | 17 min | `gst_queue_loop` (idle) | 61 | `welcome` |
> | relaunch A | none | 63 s | `gst_queue_loop` | 60 | `welcome` |
> | relaunch B | none | 62 s | `gst_queue_loop` | 60 | `welcome` |
> | relaunch C | applied | 57 s | `gst_queue_loop` | 61 | `welcome` |
>
> On 1.10.0, three boots out of three wedged within 16 s. **Why 1.11.0 behaves differently
> is not known.** The TURN relay starts working in 1.11.0 (the daemon now logs
> `Refreshed 6 TURN server(s)` where 1.10.0 logged `Failed to fetch TURN credentials`),
> which changes the candidates offered to that first session. That is the one visible
> difference on this path, but it is a hypothesis: nobody tested it. Treat this as
> "not reproduced on four boots", not as fixed. The patch stays applied as insurance; with
> no deadlock it only matters if the WebRTC branch ever stops draining. IPC delivers about
> 10 frames a second here, not 30. That was not investigated, and the entry below never
> measured IPC frame rate on a healthy daemon either.

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
5. **It reproduces on every fresh process — three for three.** After quitting and reopening
   the app, the new daemon (PID 27910) was sampled ~90 s after boot and showed the **identical**
   four-thread cycle, 1879/1879, with a different session UUID. A third daemon (PID 54487,
   started 17:10) was sampled 23 minutes in and showed the same cycle again at 2086/2086, again
   with its own session UUID, and its socket probe and 8443 probe both came back 0. So all three
   of today's boots wedged the same way, each within seconds: 16 s after the 11:43 boot (after
   five short-lived sessions), 11 s after the 16:47 boot (after one), and 13 s after the 17:10
   boot. Timeline of the second: daemon up `16:47:27`; a `[Listener]` registers `16:47:28`; the
   `[Producer]` registers `16:47:30`; a session starts and the SDP exchange runs at `16:47:32`;
   that process's last signalling line, `16:47:38`.

   The third boot is worth calling out because it nearly produced a wrong conclusion. Writing
   the upstream report, a re-check of the log found signalling activity long after the 16:47
   process should have been silent — which looked at first like the server recovering. It was
   not: the app had been restarted again at 17:10 and that was a *new* process, wedging on its
   own schedule. Re-measure before believing a recovery.

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

- `reachy-mini` 1.10.0, daemon started `--desktop-app-daemon --mockup-sim` (1.11.0 did not
  reproduce it on four boots; see the update at the top of this entry)
- `gst-plugin-webrtc` (`rswebrtc`) **0.15.3-e92296285** — `webrtcsink`
- GStreamer core, `webrtcbin`, `unixfd`, `applemedia` — all **1.28.7**
- macOS 26.6.1 (25G76), arm64

**Filed as <https://github.com/pollen-robotics/reachy_mini/issues/1416>** (W20, 2026-09-12).
Check there first for a fix or a workaround before spending time on this locally.

The report is: **`webrtcsink` deadlocks during the first session's `on_remote_description_set`,
and because the daemon puts the camera IPC branch behind the same `tee` as the WebRTC branch,
the deadlock also kills the local camera socket.** It carries the two thread tables above and
the version list; there is no exploit and nothing to weaponise. The published text is
deliberately cleaner than this entry: no PIDs, no wall-clock timestamps, no local socket path,
and the timeline rebased to relative offsets, because an upstream issue is public and permanent
while this file is not.
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

**What it costs us.** Milestone 4 (face recognition) wants camera frames. D24 concluded the
daemon's camera path on this Mac was unusable and that one should "prototype against a camera
directly instead". **W27 measured otherwise on 2026-09-14, and that verdict is withdrawn — the
mechanism below stands, but it is conditional rather than permanent.** What was measured, against
the desktop app running `--mockup-sim`, with `/api/media/status` reporting
`{"available":true,"released":false}`:

Both reads were measured, because both are used: `media.get_frame()` is what `faces/capture.py`
calls for a raw BGR array, and `media.get_frame_jpeg()` is what the `camera` tool calls for the
LLM's vision input. They behave identically, which is why they carry the same attempt budget.

| Measured | `get_frame()` | `get_frame_jpeg()` |
|---|---|---|
| one read, cold | `(720, 1280, 3)` `uint8` BGR, in 0.00s | ~250 KB of JPEG |
| 10 reads back to back | 3 / 10 | 3 / 10 |
| 6 reads at 0.05s spacing | 4–5 / 6 (two runs; this is the jittery boundary) | 5 / 6 |
| 6 reads at 0.10s spacing | **6 / 6** | **6 / 6** |
| 18 reads at ≥ 0.10s spacing | **18 / 18** | **18 / 18** |

So the path works, and `(720, 1280, 3)` `uint8` is exactly what `faces/embedding.py` consumes.

**Two traps, both of which produce D24's symptom.** Knowing them is what turns that entry from a
dead end into a procedure:

1. **A `None` frame usually means "too early", not "no camera".** `get_frame()` waits 20ms for a
   sample and the camera produces one every ~33ms at 30fps, so a read issued straight after a
   successful one misses by construction. Only `media.camera is None` means there is no camera.
   `faces/capture.py` is the module that tells those apart; do not re-derive the check.
2. **`ReachyMini(media_backend="no_media")` against a live daemon RELEASES the daemon's media.**
   Measured before/after: `/api/media/status` went `{"available":true,"released":false}` →
   `{"available":false,"released":true}` and stayed there. This is the trap, because
   `is_local_camera_available()` is a test for `/tmp/reachymini_camera_socket`, and once media is
   released that socket goes away — so the *next* `media_backend="default"` auto-detects **WebRTC
   instead of LOCAL**, and the WebRTC branch is the one that deadlocks on `ws://localhost:8443`
   (observed again in W27: blocked until killed). A headless script run earlier in a session is
   therefore enough to make the camera look permanently broken for everything after it.

   Recover with `curl -X POST http://localhost:8000/api/media/acquire`, which restored
   `{"available":true,"released":false}` both times it was needed.

So: pin `media_backend="local"` in any probe script, never use `"no_media"` against a running
desktop app, and check `/api/media/status` before concluding the camera is dead.

The daemon's own face detection was not re-tested. The `face_target` block on
`/api/daemon/status` still answered `{"detected": false, ..., "ts": null}` in D24's state — a null
`ts`, so nothing had ever been detected. (Nobody was deliberately in frame, so that is consistent
with starvation rather than proof of it.) Nothing in this app uses `face_target`; it reads frames
itself.

Which camera is worth knowing, because D22 recorded only one and there are **two**. Measured in
D24 with `system_profiler SPCameraDataType` and GStreamer's device monitor, which agree:

| Device | Notes |
|---|---|
| `USB Camera VID:1133 PID:2085` | the wired webcam; this is the one the daemon opens |
| `iPhone Camera` (`iPhone17,3`) | Continuity Camera — present only when the phone is nearby and willing |

This machine has **no built-in FaceTime camera**, so those two are the whole inventory. Whether
the daemon holds the USB webcam *exclusively* while it runs was **not** tested, so do not assume
you can open it alongside the daemon — try it, and fall back to stopping the daemon first.

#### Who owns the camera, and where it is released

Worth writing down because it is the answer to "is the camera held open between frame grabs?" —
and because the obvious place to release it is the wrong one.

The **daemon** owns the physical camera. This app never opens a webcam; it reads frames off the
daemon through the single `ReachyMini` handle built in `main.py` (or handed in by the daemon when
the app runs under it). That handle is closed **exactly once**, in `run`'s shutdown `finally`,
via `robot.media.close()`.

`faces/capture.py` therefore holds nothing open between requests **because it opens nothing** — it
borrows the handle, calls `get_frame()`, and returns. It deliberately does not close or release
anything, and that is not an oversight:

- `MediaManager.close()` closes the **audio** device as well as the camera, and that audio device
  is the tutor's voice. A frame grab that released it would mute the lesson mid-sentence.
- `ReachyMini.release_media()` is the daemon-level release behind `POST /api/media/release`.
  Calling it per frame would tear down and rebuild audio on every recognition attempt — and, per
  the trap above, would leave the next auto-detect resolving to the WebRTC path.

Both halves are tested rather than asserted here: one test pins `get_frame` as the *only* method
`capture.py` may call on the handle, and another reads `main.py` structurally to confirm the one
real release is still in its shutdown `finally`.

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
- **The TURN warnings in a 1.10.0 log are a red herring.** `Failed to fetch TURN credentials`
  for `turn.fastrtc.org` repeated every ~30 s because that domain's DNS has been dead since
  June 2026. The dashboard is a loopback peer and needs host candidates only; the daemon logged
  `No TURN servers held; offering host/srflx only` and went on to a full SDP exchange.
  **1.11.0 fixed the default** (upstream #1408): the daemon now logs
  `Refreshed 6 TURN server(s)`, so on 1.11.0 a TURN *failure* in the log is new and worth
  reading, not noise.

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
