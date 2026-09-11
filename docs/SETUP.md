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

You want `"state": "running"`. Note the `"version"` — you will need it in step 4.

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

## 6. Hugging Face login

The voice backend authenticates as you. Without a token it will not start.

```bash
~/dev/reachy/reachy_mini_env/bin/hf auth login
```

No API keys are needed beyond this. The app uses the Hugging Face realtime backend, which is
free and requires no separate provider account.

## 7. Verify everything

```bash
cd ~/dev/reachy/learn_language
./scripts/check-env.sh
```

This checks every failure mode described in this document — host tools, both Python
environments, version skew, the daemon, the profile format, and your Hugging Face token — and
prints a concrete fix for anything broken. It exits non-zero on failure, so it works in CI too.

Re-run it any time something stops working. It is faster than debugging by hand, and it knows
about the traps.

## 8. Run the app

```bash
cd ~/dev/reachy/learn_language/reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main --ui --no-camera
```

- Web UI: **http://127.0.0.1:7860/**
- `--no-camera` because the simulator has no camera
- `--debug` for verbose logging

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

### No sound, or the app never speaks

Check your Hugging Face token (`./scripts/check-env.sh` covers this). Then check macOS
microphone permissions for your terminal under System Settings → Privacy & Security.

### Port 8000 or 7860 already in use

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Only one app may drive the robot at a time. Quitting the other one is usually the fix.

---

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
- **The database is the source of truth for learner progress**, not the LLM. And tools must
  take the learner's identity from app state, never from a tool argument, so nobody can talk
  their way into someone else's profile. See `../CLAUDE.md`.
- **Keep on-robot work light.** The deployment target is the Wireless model, whose onboard
  computer is much weaker than your Mac. The simulator will not reveal performance problems.

---

## Repository layout (unresolved)

Worth flagging to anyone joining: the project is **not yet in a usable repository**.

```
learn_language/              <- not a git repository
├── CLAUDE.md
├── agents.local.md
├── docs/  SETUP.md, plan.md
├── scripts/  check-env.sh
└── reachy_language_tutor/   <- a git repository, zero commits, no remote
```

Everything a developer needs is spread across both levels, but only the inner directory is
under version control — and it has no commits and nowhere to push. **Step 3 of this document
cannot actually be followed until that is resolved.**

The likely fix is to make `learn_language/` itself the repository, with the app as a
subdirectory, so that `CLAUDE.md`, `docs/`, `scripts/` and the app travel together. That is a
decision for the project owner, not something to change unilaterally.
