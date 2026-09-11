# Reachy Mini language-learning app: planning notes

*Compiled September 2026. Prices, versions, and software details change often, so check the official docs before acting on them.*

## 1. The concept

A Reachy Mini app that helps people learn a new language. Reachy recognizes the person in front of it, pulls up their learner profile, and knows which languages they have worked on. When the person says which language they want to practice, Reachy looks up the lessons they have completed and the ones that remain, then runs the next lesson as a spoken conversation.

The target deployment is about 20 unrelated individuals, each with a Reachy Mini in their own home.

## 2. About Reachy Mini

Reachy Mini is an open-source desktop robot from Pollen Robotics, a French startup acquired by Hugging Face in April 2025. It is built for conversation and expression rather than physical tasks. It has a moving head, two animated antennas, a camera, microphones, a speaker, and a rotating body. It has no arms.

| Version | Price (USD) | How it works |
| --- | --- | --- |
| Lite | $399 | Plugs into a Mac or Linux computer, which does the processing |
| Wireless | $499 | Onboard computer, Wi-Fi, battery, and motion sensor; runs on its own |

Both ship as DIY kits that take roughly 2 to 3 hours to assemble. The official store ships to the US and Canada, with delivery of up to 90 days and import duties added at checkout.

Buy only from pollen-robotics.com or Seeed Studio, the official distributor. Several look-alike sites show outdated prices.

Pollen also makes Reachy 2, a full-size research humanoid that costs about $70,000. It is not relevant to this project.

## 3. The development platform

Reachy Mini has an app store built on Hugging Face Spaces. Developers publish apps as Spaces, and any owner can install them from the robot's dashboard with one click.

Ways to build:

- **Python SDK:** full control of movement, camera, and audio.
- **App assistant CLI:** `reachy-mini-app-assistant` generates, checks, and publishes a correctly structured app. It offers a default template and a conversation template, which includes the audio pipeline, LLM tools, and movement coordination.
- **JavaScript SDK:** browser apps that control the robot from any device, including a phone.
- **REST and WebSocket API:** the robot's background service (the daemon) exposes full control over HTTP at `localhost:8000` (Lite and simulation) or `reachy-mini.local:8000` (Wireless).
- **AI coding agents:** Claude Code, Cursor, and similar tools can build apps when pointed at the project's `AGENTS.md` file on GitHub.

Key app rules:

- Only one app runs at a time.
- When an app stops, the robot returns to its default position.
- On the Wireless model, apps run on the robot itself.
- The recommended place for API keys and server addresses is a settings web page inside the app.

## 4. Simulation setup on a Mac

The simulator runs on MuJoCo, a physics engine, and behaves like the Lite version. Apps run inside it, so you can build and test without owning a robot. Apple Silicon and Intel Macs both work.

Requirements: Python 3.10 to 3.12 (3.12 recommended), Git, Git LFS, and the uv package manager.

```bash
# Install Homebrew first if needed: https://brew.sh
brew install git git-lfs
git lfs install
curl -LsSf https://astral.sh/uv/install.sh | sh
# Close and reopen Terminal, then:
uv python install 3.12 --default
uv venv reachy_mini_env --python 3.12
source reachy_mini_env/bin/activate
uv pip install "reachy-mini[mujoco]"
mjpython -m reachy_mini.daemon.app.main --sim
```

Mac-specific notes:

- macOS needs the `mjpython` launcher to show the 3D window, instead of the usual `reachy-mini-daemon --sim`.
- If the MuJoCo install fails with uv, the docs suggest using plain pip for the MuJoCo packages.
- Open http://localhost:8000 to confirm the dashboard is running. Keep that Terminal window open.
- Add `--scene minimal` for a scene with a table and objects instead of empty space.

## 5. Example app

Create the app structure with the CLI rather than by hand:

```bash
reachy-mini-app-assistant create look_around .
```

Then put this in the generated `main.py`. It sweeps the head side to side with a gentle tilt while the antennas wiggle:

```python
import threading, time
import numpy as np
from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini.utils import create_head_pose

class LookAroundApp(ReachyMiniApp):
    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        start = time.time()
        while not stop_event.is_set():  # set when the app is stopped
            t = time.time() - start
            yaw = 30 * np.sin(2 * np.pi * 0.2 * t)   # sweep left/right every 5 s
            roll = 10 * np.sin(2 * np.pi * 0.1 * t)  # slow curious tilt
            wiggle = np.deg2rad(25 * np.sin(2 * np.pi * 0.5 * t))
            reachy_mini.set_target(
                head=create_head_pose(yaw=yaw, roll=roll, degrees=True),
                antennas=np.array([wiggle, -wiggle]),
            )
            time.sleep(0.02)  # ~50 updates per second

if __name__ == "__main__":
    app = LookAroundApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
```

- `run()` is the only required method. The robot is already connected when it is called.
- The `__main__` block is required, because the daemon launches the app as a module.
- With the simulator running, `python -m look_around.main` starts the app.

Example apps worth studying: Radio (antennas as buttons), Spaceship Game (head as a joystick), Hand Tracker (camera-based real-time control), and the Conversation App (LLM tools and audio).

## 6. Connecting to LLMs

Apps are ordinary Python programs, so they can call any LLM API. The model does not run on the robot; the Wireless model's onboard computer is too small. It runs in the cloud or on a separate computer.

The official Conversation App supports several voice backends:

- **Hugging Face realtime (default):** uses a built-in Hugging Face server or your own local endpoint, with no API key required.
- **OpenAI Realtime:** requires an OpenAI API key.
- **Gemini Live:** requires a Gemini API key.

Hugging Face's open-source speech-to-speech pipeline chains four steps: detecting speech, converting speech to text, running the LLM, and converting the reply to speech. It powers conversations on thousands of Reachy Minis. The LLM step is swappable:

- **Hosted providers:** any provider that supports the protocol, such as OpenRouter, Together, or Fireworks.
- **On a Mac:** Hugging Face recommends a small Qwen3 model running through MLX, which responds almost instantly on Apple Silicon.

By default, conversations go to a cloud server. Running the model locally or on a chosen host keeps control of the data.

## 7. MCP support

MCP works in both directions:

- **Reachy using MCP tools:** the Conversation App can add remote tools hosted as MCP servers on Hugging Face Spaces, such as the weather, time, and search examples Pollen publishes. The command `reachy-mini-conversation-app tool-spaces add <owner/space-name>` installs a tool Space and enables it. Personality profiles control which tools each assistant can use. For other MCP servers, a custom Python app can use the MCP client library directly.
- **AI assistants controlling Reachy:** community-built MCP servers let Claude Desktop, Claude Code, and other clients control head movement, antennas, gestures, emotions, and the camera through plain language. They work with the simulator. They are community projects, so quality varies.

## 8. Architecture for home users

```
EACH HOME (x20)                         YOUR BACKEND (shared)
+---------------------------+           +----------------------------+
| Reachy Mini (Wireless)    |  HTTPS    | Accounts + progress        |
|  - Language app           | --------> | Lesson catalog             |
|  - Household faceprints   |           | LLM proxy                  |
|    (never leave the home) |           +-------------+--------------+
+---------------------------+                         |
                                                      v
                                              LLM provider (voice model)
```

### Session flow

1. Reachy sees a face and matches it against the household's enrolled members.
2. It greets the person by name and mentions the languages they have practiced.
3. The person says, for example, "Spanish."
4. The LLM calls a progress tool and gets back something like "6 of 12 lessons done; next is ordering food."
5. Reachy runs the lesson as a conversation, recording results as it goes, with emotions and antenna movements for feedback.

### Components

- **Robot:** the Wireless model is the natural fit because it runs on its own and needs no computer beside it. The app runs on the robot.
- **Face recognition:** it only has to tell apart the few people in one home, which is far more reliable than matching against a large group. Faceprints (numeric face data, not photos) stay on the robot. If most homes have one learner, face recognition could be replaced by Reachy asking "Who's practicing today?" Test early on a real Wireless unit whether it runs fast enough on the onboard computer; the simulator runs on a Mac and won't show this.
- **Voice and LLM loop:** start from the conversation template. Test speech recognition and pronunciation in every target language before choosing a backend.
- **Learner tools:** functions such as `get_profile`, `get_progress(language)`, and `record_result`. They run inside the app and call the backend over HTTPS.
- **Backend:** a small hosted database and API for accounts, progress, and the lesson catalog. Twenty users is a very light load. Central progress survives a robot reset and could support a phone app later.
- **LLM proxy:** the robots call your backend, which calls the LLM provider. This keeps the API key out of the app, where anyone could extract it, and allows per-user usage caps.

### Key design decisions

- **The app sets the learner ID, not the LLM.** Tools use the identity from face recognition instead of accepting a name from the conversation, so nobody can talk their way into someone else's profile.
- **The database is the source of truth for progress.** The LLM teaches and chats; completed and next lessons come from the database.
- **Costs scale with practice time.** Voice models bill by usage, so estimate practice minutes per learner before choosing a provider.

### Distribution

Publish the app once as a Hugging Face Space. Each user installs it from their robot's dashboard and signs in to their account on the app's settings page. Updates are published centrally.

### Privacy

Only names, emails, and learning progress leave the home. Faceprints stay on each robot. Still needed:

- A clear privacy policy and a way to delete accounts and data.
- Parental consent if children in these homes will use it.

## 9. Appendix: shared public locations

If the robots were ever placed in shared settings such as classrooms or libraries, the design would change:

- Recognition would need to work across sites, which means a central faceprint database. That is the highest-risk element of that design.
- Faceprints should be computed on-site and only those sent for matching, so no video leaves the building. This likely needs a small computer beside each robot.
- Each robot needs its own revocable credential, and robots should work gracefully when the connection drops.
- A learner portal would handle enrollment, consent, and a QR code or PIN alternative to face recognition.
- Canada's privacy commissioners have acted against facial recognition used without meaningful consent (the 2020 Cadillac Fairview mall directory case is the standard example). Face recognition should be opt-in, deletable on request, and hosted in Canada. Consult a privacy lawyer before any public launch.

## 10. Open questions and next steps

- Which languages will be offered first, and does the chosen voice backend handle them well?
- Will most homes have one learner or several? This decides whether face recognition is needed.
- Which LLM provider fits the budget at the expected practice minutes?
- Next: set up the simulator, build a simple app, then design the database tables and learner tools.

## Sources

- Reachy Mini documentation: https://huggingface.co/docs/reachy_mini
- Reachy Mini SDK on GitHub: https://github.com/pollen-robotics/reachy_mini
- Building and publishing apps: https://huggingface.co/docs/reachy_mini/SDK/apps
- Simulation setup: https://huggingface.co/docs/reachy_mini/platforms/simulation/get_started
- Conversation App: https://github.com/pollen-robotics/reachy_mini_conversation_app
- Adding MCP tools to Reachy Mini: https://huggingface.co/blog/adding-mcp-tools-to-reachy-mini
- Running conversations locally: https://huggingface.co/blog/local-reachy-mini-conversation
- Speech-to-speech pipeline: https://github.com/huggingface/speech-to-speech
