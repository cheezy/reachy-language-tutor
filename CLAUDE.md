# Reachy language tutor: project brief

## What we're building

A Reachy Mini app that helps people learn a new language. Reachy recognizes the person in front of it, loads their learner profile, and knows which languages they have worked on. When the person says which language they want to practice, Reachy looks up completed and remaining lessons and runs the next lesson as a spoken conversation, using expressive movement for feedback.

Target deployment: about 20 unrelated people, each with a Reachy Mini Wireless in their own home.

Full planning notes: `docs/plan.md`

## Development environment

- Mac, with the Reachy Mini Control desktop app running the robot in simulation mode (lightweight "mockup-sim").
- The robot service listens on `localhost:8000`.
- Python environment: `~/dev/reachy/reachy_mini_env`. The SDK version matches the desktop app's robot service (1.8.0). Don't upgrade `reachy-mini` without checking the app's service version first.
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
