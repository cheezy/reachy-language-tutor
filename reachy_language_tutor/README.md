---
title: Reachy Language Tutor
emoji: 🤖
colorFrom: purple
colorTo: gray
sdk: static
pinned: false
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Reachy Language Tutor

A Reachy Mini app for practising a new language by talking to the robot. Reachy identifies
the learner, looks up which lessons they have finished, and runs the next one as a spoken
conversation, using movement for feedback.

Built on the Reachy Mini conversation app.

## Where things live

This directory is the app, and it is also the root of the published Hugging Face Space —
everything here ships to the robot. The wider project (planning notes, developer setup,
tooling) lives one level up in the repository.

| Path | What it is |
|---|---|
| `profiles/_reachy_language_tutor_locked_profile/profile.md` | The tutor's identity, teaching style and enabled tools |
| `src/reachy_language_tutor/tools/` | Tool implementations, one `Tool` subclass per file |
| `src/reachy_language_tutor/main.py` | App entry point (`ReachyLanguageTutor`) |
| `src/reachy_language_tutor/static/` | The app's settings web UI |
| `index.html` | The Hugging Face Space landing page |

## Customising the tutor

The app is locked to a single profile, so there is no personality switcher. Edit
`profiles/_reachy_language_tutor_locked_profile/profile.md`:

```
+++
schema_version = 1
default_tools = ["play_emotion", "move_head", "head_tracking"]
+++

## IDENTITY
You are Reachy Mini, a patient language-practice partner.
```

TOML front matter between `+++` markers declares the schema version and which tools the
profile may use; everything after it is the system prompt. Tool names must match a `Tool`
subclass in `src/reachy_language_tutor/tools/`, or the app will not start.

> An older profile format used separate `instructions.txt` and `tools.txt` files. It is no
> longer read. If you find those files anywhere in this app, they are dead.

## Running it

```bash
python -m reachy_language_tutor.main --ui --no-camera
```

The settings UI is served at <http://127.0.0.1:7860/>. Use `--no-camera` when running against
the simulator, which has none.

Full setup instructions — including the SDK/daemon version trap that breaks all motion — are
in `docs/SETUP.md` at the repository root. Run `scripts/check-env.sh` to verify an environment.

## Notes

- `README_OLD.md` is the original conversation app README, kept for reference.
- Secrets belong in `.env`, which is gitignored. Nothing is required there today; the voice
  backend authenticates through your Hugging Face CLI login.
