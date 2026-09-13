# Reachy Mini language-learning app: planning notes

*Compiled September 2026. Prices, versions, and software details change often, so check the official docs before acting on them.*

## 1. The concept

A Reachy Mini app that helps people learn a new language. Reachy recognizes the person in front of it, pulls up their learner profile, and knows which languages they have worked on. When the person says which language they want to practice, Reachy looks up the lessons they have completed and the ones that remain, then runs the next lesson as a spoken conversation.

The target deployment is unrelated individuals, each with a Reachy Mini in their own home. How many
is deliberately open — it could be tens or thousands, and the design should not have to change when
the answer arrives. The properties that matter follow from one household per robot, not from a total.

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

## 9. Lesson content: the FSI/DLI public-domain courses

The architecture above says "lesson catalog" and never says what is in it. Milestone 2
shipped a catalog that is really a *syllabus*: five languages, six lessons each, and for
each lesson a title and a one-sentence objective of about seventy characters. There is no
content column. The tutor improvises the entire lesson from that sentence plus five lines
of teaching style, which means no two runs of the same lesson are alike and nothing
constrains what a child is actually taught.

> **Where this now stands.** The three paragraphs above describe what milestone 2 shipped,
> and are kept because they are the argument for the decision below. They no longer
> describe the app: W23 added the content tables, W24 converted six units of Italian FAST
> into them, and W18 added the `get_lesson_content` tool and rewrote the locked profile, so
> the tutor teaches a converted unit from its own dialogue, notes and drills. Italian is
> the only language converted so far; the other four still hold title-and-objective
> lessons, which the tutor still improvises around.

**Decision: base lesson content on the Foreign Service Institute and Defense Language
Institute courses** published at https://www.fsi-language-courses.org/fsi-courses/.

### Why they fit

Their structure is almost exactly what a speaking robot needs. Measured from *Italian
Headstart, Modules 1-3* (283 pages), one unit is:

1. **Conversazione** - a short dialogue, named speakers, target language only.
2. **Notes on the conversation** - numbered usage and grammar points, in English.
3. **Exercises** - typed, and two of the types are directly runnable as speech:
   *Repetition* (term and gloss, side by side) and a cue-response drill printed as
   "You hear: morning/Capitano - You say: Buon giorno, Signor Capitano."

That last one is a spoken exchange with a right answer. It is what our tutor is for, and
it is why these courses are worth the work of ingesting rather than writing lessons from
scratch.

Coverage is good for our five languages: French (Basic, Fast, Headstart), German (Basic,
Fast, Headstart, Programmed), Italian (Fast, Headstart, Programmed), Portuguese (Brazilian
Fast, Programmatic), Spanish (Basic, Fast, Programmatic, three Headstarts).

### Licensing - free, but not uniformly, and it must be checked per course

FSI and DLI materials are works of the US federal government and so carry no copyright in
the United States (17 U.S.C. 105). The site states plainly: "These courses are in the
public domain and free to use."

Two exceptions matter and neither is theoretical:

- **Some FSI courses were produced under contract and ARE copyrighted.** Russian and
  Japanese are the commonly cited examples. Public domain is a property of a particular
  course, not of the FSI label.
- **Repackaged and edited editions carry their own copyright** in the derivative work.
  Take material from the original scans, not from a cleaned-up commercial reissue.

So each course we actually use gets its provenance and rights status recorded before a
single line of it reaches the database.

### Three measured constraints, all of which shape the work

1. **These are scans, not documents.** *Italian Headstart Modules 1-3* is 277 JBIG2
   images across 283 pages. There is an OCR text layer and `pdftotext -layout` preserves
   the two-column drill structure well.

2. **The OCR loses accents, which for a language course is the worst possible failure.**
   Measured across 101 pages: 93 accented characters survived and 58 were replaced by a
   stray `~` - roughly four in ten lost. "Il piacere e mio" instead of "Il piacere e' mio"
   is not a typo in this application, it is teaching a child the wrong word. Also seen:
   "THE" read as "TUE" (4 times) and list markers "1." read as "l." (19 times). **The OCR
   layer cannot be bulk-imported.** Either re-OCR at higher quality with an
   accent-aware engine, or correct per unit against the page image, and in both cases
   verify against the scan before anything ships.

3. **The content is military and forty years old.** *Italian Headstart* was written by the
   Defense Language Institute in 1985 for US service personnel. Unit 1's vocabulary is
   ranks - Generale, Ammiraglio, Colonnello, Sergente - and its dialogue is two naval
   officers introducing a wife. One note reads: "Women do not serve in the armed forces of
   Italy, but there are women in the police forces." That was already being overtaken in
   1985 and Italy opened its armed forces to women in 1999. It is false today, and it is
   not what these robots should be saying in family homes.

### Source decision: Basic and FAST, never Headstart

**Use the FSI Basic and FAST courses. Do not use the Headstart series.** Headstart is
Defense Language Institute material written for service personnel, and it shows: Italian
Headstart Unit 1 teaches ranks, its dialogue is two naval officers, and one note claims
women do not serve in Italy's armed forces. None of that belongs in a family home.

FAST stands for Familiarization And Short-Term Training. Italian FAST was published by
FSI in 1992 for diplomatic staff: 30 lessons over 32 units, dialogues set in different
Italian regions, aimed at everyday situations. Measured against Headstart over comparable
samples, it is better on both counts that matter:

| | Headstart (DLI, 1985) | FAST (FSI, 1992) |
|---|---|---|
| Military terms per ~120 pages | pervasive; Unit 1 *is* ranks | **0** |
| Accented characters lost to OCR | 38% (58 bad / 93 good) | **19%** (79 bad / 333 good) |

The FAST scans also carry a different OCR pathology worth knowing about: `l` is frequently
read as `/`, giving `usual/y`, `on/y`, `se/ecting` - 194 instances in 121 pages. Unlike the
accent loss this is largely a mechanical repair, but not blindly: `americano/a` and `s/he`
are legitimate slashes in the source.

**The language itself is natural and still current.** *Scusi, Lei è americano?*, *Ha
qualcosa da dichiarare?*, *No, non ho niente da dichiarare* - all idiomatic Italian today,
thirty years on. The drills are substitution models with a question and an expected answer,
ten or so variations each, which is directly runnable as speech.

### What still has to change, and it is the situations rather than the sentences

FAST is written for an adult diplomat arriving at post, and that premise surfaces in the
content even though the grammar and register are fine:

- **Scenarios.** Lesson II is going through customs - *doganiere*, *guardia di finanza*,
  *stecche di sigarette*, *liquori*, *profumi*. Duty-free allowances are not early
  vocabulary for a child.
- **Identity.** Drills practise *Sono dell'Ambasciata*, "I am from the Embassy." Nobody
  using this app is.
- **Dated practicalities.** A 1992 customs lesson describes a border regime the EU single
  market replaced in 1993.
- **A human instructor.** The text says things like "with the instructor taking the part of
  the Italian." Our tutor *is* that instructor, so the framing has to be rewritten rather
  than transcribed.

So the modernisation pass is real work, but it is narrower than it first looks: keep the
sentences and the drill structure, replace the situations. A customs queue becomes a
shop, a station, a kitchen. *Sono dell'Ambasciata* becomes something a person in a house
would actually say.

### What follows from that

**Curate, do not import.** The asset is the *structure*, the *method* and the *sentences*;
the situations need replacing and the text needs correcting. A realistic shape:

- Prove the pipeline on **one** language end to end before touching the other four.
- Keep a provenance record per lesson: course, module, unit, page. It is what makes a
  correction auditable and a rights question answerable later.
- Record every curation decision - what was replaced and why - so a human can review the
  judgement rather than only the result.
- The audio (13+ hours for Italian FAST alone) is native-speaker reference. The tutor
  speaks through realtime TTS and does not need it now. Do not ingest it yet.

### What the first conversion actually found

Italian was converted from *Italian FAST* Volume 1. The method is written up in
[converting-a-course.md](converting-a-course.md) and every judgement in
[curation-log-italian-fast.md](curation-log-italian-fast.md); what is worth carrying back
into this section is what the estimates above got right and wrong.

**Right: the OCR figures.** Measured again over the same page range, independently of the
sample that produced the numbers above: 333 accented characters correct, 79 replaced by a
stray `~`. The 19% loss is real and it is why every shipped line was read on the page
image rather than out of the text layer.

**Missed: two more OCR faults, and one of them is dangerous.** Beyond the `l`→`/` fault
already recorded, `I` is read as `!` (`FS!` for FSI), and — the one to watch — `/` is
sometimes read as `l`, so `arrivato/a` arrives as `arrivatola`. That is a plausible
Italian word shape, so unlike `usual/y` it does not announce itself as broken. The OCR
also **invents** accents: `SETTING THE SCENE` came through as `SCENÈ`. A conversion that
only added missing accents would have shipped that one.

**Underestimated: how much the no-uniformed-authority rule removes.** Twelve of the
eighteen units in Volume 1 did not survive it. The airport, the currency exchange and
three of the four hotel units are all built on arrival formalities or an embassy booking;
what remains is the ordinary-life core — asking the time, room service, a taxi, a clothes
shop, a restaurant, and a phone call about a flat. Six units shipped, and that is a
reasonable yield to expect per volume rather than a disappointing one.

**Not anticipated: the currency.** The course is priced in lire throughout, a currency
withdrawn in 2002. The conversion keeps the source's digits and drops the thousands, so
`120.000 lire` becomes `120 euro` — which is a plausible price today but neither the same
number nor the same spoken line, and that trade is recorded per unit rather than waved
through as a formatting change.

**Still missing: a native speaker.** The Italian that ships is the source's own, checked
character by character against the page images. That is a different and weaker claim than
"a teacher reviewed it", and it is the gap a household would notice first.

## 10. Appendix: shared public locations

If the robots were ever placed in shared settings such as classrooms or libraries, the design would change:

- Recognition would need to work across sites, which means a central faceprint database. That is the highest-risk element of that design.
- Faceprints should be computed on-site and only those sent for matching, so no video leaves the building. This likely needs a small computer beside each robot.
- Each robot needs its own revocable credential, and robots should work gracefully when the connection drops.
- A learner portal would handle enrollment, consent, and a QR code or PIN alternative to face recognition.
- Canada's privacy commissioners have acted against facial recognition used without meaningful consent (the 2020 Cadillac Fairview mall directory case is the standard example). Face recognition should be opt-in, deletable on request, and hosted in Canada. Consult a privacy lawyer before any public launch.

## 11. Open questions and next steps

- Which languages will be offered first, and does the chosen voice backend handle them well?
- Re-OCR the scans, or correct the existing text layer unit by unit? Section 9 measures the
  accent loss that makes this a real decision rather than a detail.
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
- FSI and DLI language courses (public domain): https://www.fsi-language-courses.org/fsi-courses/
