import re
import random
import logging
import unicodedata
from typing import TYPE_CHECKING, Any, Dict

from reachy_language_tutor.logging_safety import where, log_safe
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


if TYPE_CHECKING:
    from reachy_mini.motion.recorded_move import RecordedMoves


logger = logging.getLogger(__name__)

try:
    from reachy_mini.motion.recorded_move import RecordedMoves
    from reachy_language_tutor.dance_emotion_moves import EmotionQueueMove

    EMOTION_AVAILABLE = True
except Exception as e:
    logger.warning("Emotion library not available: %s", log_safe(e))
    EMOTION_AVAILABLE = False


EMOTION_INTENTS: tuple[str, ...] = (
    "random",
    "happy",
    "excited",
    "loving",
    "grateful",
    "success",
    "thinking",
    "attentive",
    "confused",
    "uncertain",
    "sad",
    "downcast",
    "lonely",
    "angry",
    "irritated",
    "displeased",
    "disgusted",
    "scared",
    "anxious",
    "surprised",
    "amazed",
    "calming",
    "relief",
    "impatient",
    "embarrassed",
    "bored",
    "tired",
    "sleepy",
    "yes",
    "yes_understanding",
    "no",
    "no_sad",
    "no_excited",
    "no_firm",
    "welcoming",
    "greeting",
    "goodbye",
    "go_away",
    "helpful",
    "dance",
    "electric",
    "dying",
)

_EXCELLENT_MOVES: tuple[str, ...] = (
    "anxiety1",
    "boredom2",
    "dance2",
    "dance3",
    "downcast1",
    "dying1",
    "exhausted1",
    "grateful1",
    "helpful1",
    "loving1",
    "rage1",
    "reprimand1",
    "resigned1",
    "sad1",
    "sad2",
    "scared1",
    "sleep1",
    "surprised1",
    "thoughtful1",
    "welcoming2",
)

_OK_CLEAR_MOVES: tuple[str, ...] = (
    "amazed1",
    "attentive1",
    "attentive2",
    "boredom1",
    "confused1",
    "disgusted1",
    "displeased1",
    "displeased2",
    "fear1",
    "impatient2",
    "irritated1",
    "irritated2",
    "laughing1",
    "laughing2",
    "lonely1",
    "no1",
    "no_excited1",
    "no_sad1",
    "reprimand2",
    "shy1",
    "success1",
    "success2",
    "surprised2",
    "thoughtful2",
    "uncertain1",
    "understanding2",
    "yes1",
)

_CURATED_DEFAULT_MOVES: tuple[str, ...] = _EXCELLENT_MOVES + _OK_CLEAR_MOVES

_INTENT_TO_MOVES: dict[str, tuple[str, ...]] = {
    "happy": ("laughing2", "laughing1"),
    "excited": ("dance3", "dance2"),
    "loving": ("loving1",),
    "grateful": ("grateful1",),
    "success": ("success1", "success2"),
    "thinking": ("thoughtful1", "thoughtful2"),
    "attentive": ("attentive1", "attentive2"),
    "confused": ("confused1",),
    "uncertain": ("uncertain1",),
    "sad": ("sad1", "sad2", "downcast1"),
    "downcast": ("downcast1", "sad1"),
    "lonely": ("lonely1",),
    "angry": ("rage1", "irritated2", "irritated1"),
    "irritated": ("irritated1", "irritated2", "displeased2"),
    "displeased": ("displeased1", "displeased2"),
    "disgusted": ("disgusted1",),
    "scared": ("scared1", "fear1", "anxiety1"),
    "anxious": ("anxiety1", "fear1", "scared1"),
    "surprised": ("surprised1", "surprised2", "amazed1"),
    "amazed": ("amazed1", "surprised1"),
    "calming": ("calming1",),
    "relief": ("relief1", "relief2"),
    "impatient": ("impatient2",),
    "embarrassed": ("shy1",),
    "bored": ("boredom2", "boredom1"),
    "tired": ("exhausted1", "sleep1"),
    "sleepy": ("sleep1", "exhausted1"),
    "yes": ("yes1", "understanding2"),
    "yes_understanding": ("understanding2",),
    "no": ("no1",),
    "no_sad": ("no_sad1",),
    "no_excited": ("no_excited1",),
    "no_firm": ("no1",),
    "welcoming": ("welcoming2",),
    "greeting": ("welcoming2",),
    "goodbye": ("loving1", "welcoming2"),
    "go_away": ("go_away1",),
    "helpful": ("helpful1",),
    "dance": ("dance2", "dance3"),
    "electric": ("electric1",),
    "dying": ("dying1",),
}

_ALLOWED_MOVE_NAMES: frozenset[str] = frozenset(_CURATED_DEFAULT_MOVES).union(*_INTENT_TO_MOVES.values())

_KEYWORD_INTENTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("no", "sad"), "no_sad"),
    (("no", "excited"), "no_excited"),
    (("no", "firm"), "no_firm"),
    (("yes", "understanding"), "yes_understanding"),
)


def _normalize_emotion_key(value: str) -> str:
    """Normalize an emotion request for exact intent and keyword matching."""
    without_accents = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", without_accents.lower()).strip("_")


def _keyword_intent(normalized_key: str) -> str | None:
    """Return the first nuanced intent whose keywords are all present."""
    tokens = set(normalized_key.split("_"))
    for keywords, intent in _KEYWORD_INTENTS:
        if all(keyword in tokens for keyword in keywords):
            return intent
    return None


# The one emotion library this process uses. Module level rather than per-tool, because
# W17's lesson_feedback needs to know whether the dataset has already been downloaded
# WITHOUT downloading it -- a second cache would answer that question wrongly.
_LOADED_LIBRARY: "RecordedMoves | None" = None


def load_emotion_library() -> "RecordedMoves":
    """Return this process's emotion library, downloading the dataset if nobody has yet."""
    global _LOADED_LIBRARY
    if _LOADED_LIBRARY is None:
        # Constructing this downloads the dataset, so it must not run at import -- and it
        # must not run anywhere a caller cannot afford to wait for a download.
        _LOADED_LIBRARY = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
    return _LOADED_LIBRARY


def loaded_emotion_library() -> "RecordedMoves | None":
    """Return the library only if something already paid to load it; never construct one.

    The read-only half of the pair. A caller on a latency-sensitive path -- the lesson
    tools, which run inside the voice loop -- asks this one, gets None on a cold process,
    and does without rather than stalling a conversation on a download.
    """
    return _LOADED_LIBRARY


def warm_emotion_library() -> bool:
    """Load the library now, at startup, and say whether it is loaded. Never raises.

    The lesson reactions in lesson_feedback ASK for the library and never build one,
    because building it downloads a dataset and they run inside the voice loop. Without
    this call nothing else would build it either until the model happened to play an
    emotion, so the first reactions of every session -- starting with the lesson-start
    nod, which is always the first -- would be silently dropped.

    Startup is where that wait belongs: it costs nobody a conversation, and it is one
    synchronous call rather than a thread, a timer or any per-frame work.

    What it costs when the network is bad, stated as measured rather than as hoped. A
    RAISED failure costs movement and nothing else -- it is caught here and the app goes
    on. A SLOW one costs time to start: huggingface_hub bounds each request, but nothing
    bounds the total across the dataset's files, so a degraded remote stretches startup
    rather than failing it. That is a late robot, never a robot that will not teach: this
    runs before the conversation exists, and a lesson cannot be waiting on it.
    """
    if not EMOTION_AVAILABLE:
        return False
    try:
        load_emotion_library()
    except Exception as e:
        logger.warning("Could not load the emotion library at startup: %s", log_safe(e))
        return False
    return True


def resolve_emotion_name(requested_emotion: object, available_emotions: list[str]) -> str | None:
    """Resolve a compact intent, nuanced yes/no phrase, or recorded move ID."""
    if not available_emotions:
        return None

    requested = str(requested_emotion or "").strip()
    if not requested:
        return None

    normalized = _normalize_emotion_key(requested)
    if not normalized or normalized == "random":
        return None

    available_by_key = {_normalize_emotion_key(name): name for name in available_emotions}
    exact_move = available_by_key.get(normalized)
    if exact_move in _ALLOWED_MOVE_NAMES:
        return exact_move

    intent = normalized if normalized in _INTENT_TO_MOVES else None
    if intent is None:
        intent = _keyword_intent(normalized)

    if intent is None:
        return None

    for candidate in _INTENT_TO_MOVES.get(intent, ()):
        if candidate in available_emotions:
            return candidate
    return None


def random_curated_emotion(available_emotions: list[str]) -> str:
    """Choose a random emotion from the curated default pool when possible."""
    curated_available = [emotion for emotion in _CURATED_DEFAULT_MOVES if emotion in available_emotions]
    if curated_available:
        return random.choice(curated_available)
    return random.choice(available_emotions)


class PlayEmotion(Tool):
    """Play a pre-recorded emotion."""

    name = "play_emotion"
    description = "Play a robot emotion matching a requested emotional intent."
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {
            "emotion": {
                "type": "string",
                "enum": list(EMOTION_INTENTS),
                "description": (
                    "Compact emotional intent to express. Choose one of the enum values. Use nuanced "
                    "labels like no_sad, no_excited, no_firm, or yes_understanding when plain yes/no "
                    "loses meaning. Use random if no clear intent fits."
                ),
            },
        },
        "required": [],
    }
    _library: "RecordedMoves | None" = None

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Play a pre-recorded emotion."""
        if not EMOTION_AVAILABLE:
            return {"error": "Emotion system not available"}

        requested_emotion = kwargs.get("emotion")

        logger.info("Tool call: play_emotion emotion=%s", requested_emotion)

        try:
            if self._library is None:
                # Kept as an instance attribute as well as a module one: tests substitute
                # a fake here, and that must keep short-circuiting before the loader.
                self._library = load_emotion_library()
            library = self._library
            emotion_names = library.list_moves()
            if not emotion_names:
                return {"error": "No emotions currently available"}

            emotion_name = resolve_emotion_name(requested_emotion, emotion_names)
            if not emotion_name:
                logger.info("play_emotion: %r did not resolve; using random curated", requested_emotion)
                emotion_name = random_curated_emotion(emotion_names)

            movement_manager = deps.movement_manager
            emotion_move = EmotionQueueMove(emotion_name, library)
            movement_manager.queue_move(emotion_move)

            return {"status": "queued", "emotion": emotion_name}

        except Exception as e:
            logger.error("Failed to play emotion: %s at %s", log_safe(e), where(e))
            return {"error": f"Failed to play emotion: {e!s}"}
