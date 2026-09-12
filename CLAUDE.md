# Reachy language tutor: project brief

## What we're building

A Reachy Mini app that helps people learn a new language. Reachy recognizes the person in front of it, loads their learner profile, and knows which languages they have worked on. When the person says which language they want to practice, Reachy looks up completed and remaining lessons and runs the next lesson as a spoken conversation, using expressive movement for feedback.

Target deployment: about 20 unrelated people, each with a Reachy Mini Wireless in their own home.

Full planning notes: `docs/plan.md`

## Development environment

- Mac, with the Reachy Mini Control desktop app running the robot in simulation mode (lightweight "mockup-sim").
- The robot service listens on `localhost:8000`.
- Python environment: `~/dev/reachy/reachy_mini_env`, on the 1.10 line. `reachy_language_tutor/pyproject.toml` holds the authoritative requirement (`reachy-mini>=1.10.0rc5`) — read it rather than trusting this line, which is the sort that goes stale. The SDK has to match the desktop app's robot service, and an app update can silently provision an older one into its own managed venv (`docs/SETUP.md`). Don't upgrade `reachy-mini` without checking the app's service version first.
- The simulation has no camera. Use `ReachyMini(media_backend="no_media")` in standalone scripts. Prototype face recognition with the Mac's built-in webcam via OpenCV until real hardware is available.
- Final target is the Wireless model, where the app runs on the robot's small onboard computer. Keep on-robot work lightweight; heavy AI runs in the cloud.

## How to work

- Read https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md first and follow its skills.
- Never create app folders by hand. Use `reachy-mini-app-assistant create --template conversation <app_name> <path>`. Don't use `--publish` until I say so. If the command fails, ask me to run it in my terminal.
- Work in small milestones and test each one in the simulator before moving on.
- Keep secrets (API keys, tokens) in `.env`, add it to `.gitignore`, and never commit it.

## Architecture decisions already made

- The voice and LLM loop is built on the conversation template.
- Learner data is accessed through tools such as `get_profile`, `get_progress(language)`, and `record_result`. They start as local custom tools backed by SQLite and will later call a hosted backend over HTTPS.
- The app sets the current learner ID from recognition. Tools must never accept a learner's identity from the conversation, so nobody can talk their way into another person's profile.
- The database is the source of truth for progress. The LLM teaches and chats; it doesn't decide what's completed.
- Faceprints (numeric face data, never photos) stay on the device. Enrollment requires consent, and learners can delete their data.
- LLM API keys are never shipped inside the app. In production, robots call the LLM through a backend proxy.

## Milestones

1. Scaffold the app from the conversation template and get Reachy talking in the simulator.
2. Add a SQLite learner database with sample data and the learner tools, with the current learner hard-coded.
3. Build the lesson flow: choose a language, find the next lesson, run it, record results, and react with expressive movements.
4. Prototype household face recognition and enrollment using the Mac webcam.
5. Move learner data to a hosted backend and route LLM calls through a proxy.

## Engineering rules, learned expensively

These are not general advice. Each one names defects in this repository where getting it
wrong cost review rounds or shipped a regression. Re-read them before writing a rule, a
guard, or a validator.

- **Name what is PERMITTED, never what is forbidden.** A list of bad cases is only ever as
  complete as the last person to read it. Got wrong four times: D11 twice (NOT/CASE, then
  `IIF`/`max`/`= 0`), D19 (a four-character deny-list that admitted a newline), D20 (neutered
  the two methods the task named and left eight writers reachable — a critical). Every time,
  inverting to an allow-list closed the whole family at once. When you catch yourself adding
  a case to a list of refusals, that is the signal to invert the rule instead.
  See `.claude/skills/writing-a-guard/SKILL.md`.

- **Execute it; do not reason about it.** Every bypass and regression in D11, D15, D17, D19
  and D20 was found by running something — against a real database, a real server, a real
  parser — and none by reading code. Where a differential harness was built first, the fix
  held. A verdict you reasoned your way to is a hypothesis.

- **Enumerate the whole surface before fixing the part you were handed.** D20 was asked to
  neuter two methods; the surface had 21, ten of them writers. Fixing exactly what a task
  names, without listing the set it belongs to, is how a fix becomes a regression.

- **A guard must mean the same thing as the code it protects.** D19's `.env` guard tested
  for CR/LF while the reader used `str.splitlines()`, which splits on eight more characters —
  so a value passed the guard and became a real `.env` line on the next read. Test what the
  consumer actually does, not what you assume it does.

- **Never write a specific claim you have not verified in this session** — not into a comment,
  a doc, a commit message, or a completion record. D15 recorded a false justification that
  could not be amended afterwards; D17 asserted a measured figure it had not measured; D20
  claimed three reachable methods when there were 21. If you did not measure it, say so.

- **A test must fail for the reason it names.** Assert the specific error, code or row — not
  that "something raised" or "a call returned ok". D17's round-trip test and D20's
  `pytest.raises(Exception)` both passed over live bugs. Revert the fix and confirm the
  failure is the one you claim.

- **Dispatch `stride:task-reviewer` with every field the task supplies** — `acceptance_criteria`,
  `testing_strategy`, `patterns_to_follow`, `pitfalls`, `security_considerations`. Omitting one
  makes that section come back `not_assessed` and the server rejects the completion. Got wrong
  in D17, D19 and D20; in D17 the missing `patterns_to_follow` is what hid an IPv6 bug for two
  rounds.
