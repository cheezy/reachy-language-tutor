# reachy_language_tutor — implementation plan

## What I understand you want

A Reachy Mini app for language practice, deployed to ~20 unrelated households, each with a
Wireless unit. Reachy recognises who is in front of it, loads that person's learner profile,
finds out which language they want to practise, looks up the next lesson, and runs it as a
spoken conversation with expressive movement as feedback.

Full background: `../docs/plan.md`. Architecture decisions: `../CLAUDE.md`.

## Technical approach

Built on the **conversation template** (fork of `reachy_mini_conversation_app`, upstream
`v1.0.1`), which supplies the audio pipeline, voice backend, LLM loop, tool dispatch and
movement coordination. Our work is confined to three seams:

1. **The profile** (`profiles/_reachy_language_tutor_locked_profile/profile.md`) — the tutor's
   identity, teaching style and movement rules. The app is locked to this profile
   (`config.LOCKED_PROFILE`), so end users can't switch personalities.
2. **Custom tools** — `get_profile`, `get_progress(language)`, `record_result`, following the
   `Tool` / `ToolDependencies` pattern in `src/reachy_language_tutor/tools/`.
3. **Identity injection** — the app sets the current learner ID from face recognition. Tools
   read it from app state, never from tool arguments, so nobody can talk their way into
   another person's profile.

Voice backend: **Hugging Face realtime** (the template default, no API key). Chosen to get
milestone 1 moving; swappable later without touching lesson logic.

## Milestones

| # | Goal | Status |
|---|------|--------|
| 1 | Scaffold from the conversation template, Reachy talking in the simulator | **done** |
| 2 | SQLite learner DB + `get_profile` / `get_progress` / `record_result`, learner hard-coded | not started |
| 3 | Lesson flow: choose language, find next lesson, run it, record results, expressive feedback | not started |
| 4 | Household face recognition + enrolment, prototyped on the Mac webcam | not started |
| 5 | Hosted backend for learner data, LLM calls via a proxy | not started |

## Milestone 1 scope

Deliberately narrow: prove the audio + LLM loop runs in the mockup simulator and that Reachy
speaks in character. No learner data, no lessons, no face recognition. The profile explicitly
tells the model it cannot look up progress yet, so it says so rather than inventing a lesson
number — a hallucinated "you're on lesson 6" during a demo would be worse than an honest
"I can't check that yet".

## Two upstream bugs hit while scaffolding (both worked around)

1. **`reachy-mini-app-assistant create --template conversation` failed.** The assistant in SDK
   1.8.0 clones the conversation app's `develop` branch, which upstream renamed to `main`.
   Patched locally: `reachy_mini/apps/fork_conversation.py:148`, `"develop"` → `"main"`
   (backup at `fork_conversation.py.bak`). **A reinstall of `reachy-mini` reverts this.**
2. **The generated profile was unreadable by the generated app.** The 1.8.0 assistant writes
   the pre-`profile.md` sidecar layout (`instructions.txt` + `tools.txt`) into
   `src/reachy_language_tutor/profiles/`, but upstream `v1.0.1` reads `profile.md` from the
   top-level `profiles/`. As generated, `config.py` would `sys.exit(1)` at startup with
   "LOCKED_PROFILE has no profile definition". Fixed by authoring
   `profiles/_reachy_language_tutor_locked_profile/profile.md` in the current format and
   deleting the stale sidecar directory (referenced nowhere; its `sweep_look.py` duplicated a
   builtin tool).

3. **The template needs a newer robot service than the desktop app ships.** Conversation app
   `v1.0.1` requires `reachy-mini>=1.10.0rc5`, but Reachy Mini Control 0.9.34 provisions
   `reachy_mini==1.8.0` into its own managed venv at
   `~/Library/Application Support/com.pollen-robotics.reachy-mini/.venv`. With SDK 1.10.0
   against the 1.8.0 daemon, connection succeeded but every motion command died with
   `ConnectionError: Lost connection with the server.` Restarting the desktop app does not
   change this — the version is baked into that venv.

   Resolved by upgrading in place with the app's own bundled `uv`:

   ```bash
   APP_SUPPORT=~/Library/"Application Support"/com.pollen-robotics.reachy-mini
   "$APP_SUPPORT/uv" pip install --python "$APP_SUPPORT/.venv/bin/python3" "reachy-mini==1.10.0"
   ```

   The daemon must then be fully restarted — `POST /api/daemon/restart` killed both the daemon
   and the control app, so relaunch Reachy Mini Control (`open -a "Reachy Mini Control"`).
   SDK and daemon are now both 1.10.0, mockup-sim still enabled. **A future desktop app update
   may re-provision 1.8.0; if motion starts failing again, redo this.**

4. **The assistant's "next steps" message is stale.** It prints
   `python src/reachy_language_tutor/main.py --gradio` on port 7861. The real flag is `--ui`
   and the port is **7860**.

All of the above are version skew between the SDK the desktop app ships and the conversation
app's `main`. Worth reporting upstream.

## Milestone 1 result

Verified in the mockup simulator on 2026-09-11:

- SDK 1.10.0 ↔ daemon 1.10.0, no mismatch warning, motion round-trips cleanly.
- App serves its UI on `http://127.0.0.1:7860/`.
- Hugging Face realtime session allocated via the deployed proxy; voice `Aiden`.
- Our locked profile loads and contributes exactly the tools we declared:
  `play_emotion, stop_emotion, move_head, sweep_look, head_tracking, idle_do_nothing,
  go_to_sleep` (the runtime adds `task_status` / `task_cancel`).
- First spoken utterance was in character:
  *"Hi, I'm Reachy Mini—what language would you like to practise together today?"*

Run it with:

```bash
cd reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main --ui --no-camera
```

`--no-camera` because the simulator has no camera. Expect two harmless startup complaints in
sim — `No Reachy Mini Audio USB device found!` and `ReSpeaker device not found.` — after which
audio falls back to the Mac's default mic and speakers with software echo cancellation.

### Useful discovery for milestone 4

The 1.10.0 daemon's `/api/daemon/status` now returns a `face_target` block
(`{detected, x, y, roll, ts}`) that 1.8.0 did not. The daemon does face **detection** natively.
That isn't recognition — it won't tell one household member from another — but it means
milestone 4 may only need to add the *identification* layer on top of daemon-provided detection,
rather than owning the whole camera pipeline. Worth checking before building anything with OpenCV.

## Open questions (not blocking milestone 1)

1. **Which languages first?** Decides how hard we lean on the voice backend's pronunciation
   quality, and whether HF realtime survives contact with, say, Mandarin tone.
2. **One learner per home, or several?** `../docs/plan.md` flags this as deciding whether face
   recognition is needed at all. If most homes have one learner, "Who's practising today?"
   removes milestone 4 entirely along with the faceprint privacy surface.
3. **Where does the lesson catalog live?** Shipped in the app, or served from the backend?
   Affects whether lesson content updates need an app republish.
4. **Expected practice minutes per learner per week?** Voice models bill by usage; this sets
   the budget and therefore the provider choice in milestone 5.
5. **Do any households include children?** Triggers parental-consent requirements before
   milestone 4's enrolment flow is designed, not after.
