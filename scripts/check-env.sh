#!/usr/bin/env bash
# Verify a development environment for the Reachy Mini language tutor.
# Checks the things that have actually broken for us, not a generic checklist.
# Safe to re-run at any time. Exits non-zero if anything is wrong.

set -uo pipefail

VENV="${REACHY_VENV:-$HOME/dev/reachy/reachy_mini_env}"
APP_SUPPORT="$HOME/Library/Application Support/com.pollen-robotics.reachy-mini"
DAEMON="${REACHY_DAEMON:-http://localhost:8000}"

pass=0; fail=0; warn=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn+1)); }
fix()  { printf '        \033[2m-> %s\033[0m\n' "$1"; }

echo
echo "Reachy Mini language tutor - environment check"
echo "============================================="

# ---------------------------------------------------------------- host tools
echo
echo "Host tools"
for c in git git-lfs uv; do
  if command -v "$c" >/dev/null 2>&1; then ok "$c present"
  else bad "$c missing"; fix "brew install ${c/git-lfs/git-lfs}"; fi
done

# ------------------------------------------------------------------ dev venv
echo
echo "Development virtualenv  ($VENV)"
if [ ! -x "$VENV/bin/python" ]; then
  bad "no virtualenv at $VENV"
  fix "uv venv \"$VENV\" --python 3.12"
  echo; echo "Cannot continue without the virtualenv."; exit 1
fi
ok "virtualenv exists"

PYV=$("$VENV/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
case "$PYV" in
  3.10|3.11|3.12) ok "python $PYV (supported)" ;;
  "")             bad "could not run python in the virtualenv" ;;
  *)              bad "python $PYV is outside the supported 3.10-3.12 range" ;;
esac

SDK=$("$VENV/bin/python" -c 'import reachy_mini;print(reachy_mini.__version__)' 2>/dev/null)
if [ -n "$SDK" ]; then ok "reachy_mini SDK $SDK"
else bad "reachy_mini not importable in the virtualenv"; fix "\"$VENV/bin/pip\" install 'reachy-mini[mujoco]'"; fi

for t in pytest ruff; do
  if [ -x "$VENV/bin/$t" ]; then ok "$t installed"
  else
    bad "$t missing - the after_doing Stride hook will fail"
    fix "\"$VENV/bin/pip\" install pytest pytest-asyncio 'ruff==0.15.20'"
  fi
done

if "$VENV/bin/python" -c 'import reachy_language_tutor' 2>/dev/null; then
  ok "reachy_language_tutor importable"
else
  bad "reachy_language_tutor not installed"
  fix "cd reachy_language_tutor && \"$VENV/bin/pip\" install -e ."
fi

# ------------------------------------------------------------- daemon + skew
echo
echo "Robot service (daemon)"
STATUS=$(curl -s -m 5 "$DAEMON/api/daemon/status" 2>/dev/null)
if [ -z "$STATUS" ]; then
  bad "daemon not responding at $DAEMON"
  fix 'open -a "Reachy Mini Control"   (and wait ~15s)'
else
  DV=$(printf '%s' "$STATUS" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
  ok "daemon responding, version $DV"

  # The failure that cost us the most time: SDK and daemon on different versions.
  # Symptom is NOT a clean error - it connects, then every motion command dies
  # with "ConnectionError: Lost connection with the server."
  if [ -n "$SDK" ] && [ -n "$DV" ]; then
    if [ "$SDK" = "$DV" ]; then
      ok "SDK and daemon versions match ($SDK)"
    else
      bad "VERSION SKEW: SDK $SDK vs daemon $DV - motion will fail"
      fix "\"\$APP_SUPPORT/uv\" pip install --python \"\$APP_SUPPORT/.venv/bin/python3\" 'reachy-mini==$SDK'"
      fix 'then relaunch: open -a "Reachy Mini Control"'
      fix 'see docs/SETUP.md, "Keeping the SDK and daemon in step"'
    fi
  fi

  printf '%s' "$STATUS" | grep -q '"mockup_sim_enabled":true' \
    && ok "running in mockup simulation" \
    || note "not in mockup-sim (fine on real hardware)"

  printf '%s' "$STATUS" | grep -q '"face_target"' \
    && ok "daemon exposes face_target (needed for milestone 4)" \
    || note "no face_target in status - daemon predates 1.10.0"
fi

# ---------------------------------------------------- managed venv (the trap)
echo
echo "Desktop app managed virtualenv"
if [ -d "$APP_SUPPORT" ]; then
  MV=$("$APP_SUPPORT/.venv/bin/python3" -c 'import reachy_mini;print(reachy_mini.__version__)' 2>/dev/null)
  [ -n "$MV" ] && ok "managed venv has reachy_mini $MV" \
               || note "could not read the managed venv's reachy_mini version"
else
  note "no managed venv - Reachy Mini Control may not be installed"
fi

# --------------------------------------------------------------- app + creds
echo
echo "Application"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/reachy_language_tutor"
PROFILE="$APP/profiles/_reachy_language_tutor_locked_profile/profile.md"

# The 1.8.0 app assistant generated an obsolete profile layout that makes the app
# sys.exit(1) at startup. Catch that here rather than in a confusing crash.
if [ -f "$PROFILE" ]; then
  ok "locked profile present (profile.md format)"
else
  bad "missing $PROFILE"
  fix 'the app exits at startup without it - see docs/SETUP.md "Profile format"'
fi
[ -d "$APP/src/reachy_language_tutor/profiles" ] \
  && note "stale src/.../profiles/ directory present - obsolete layout, safe to delete"

if "$VENV/bin/python" -c 'from huggingface_hub import get_token; import sys; sys.exit(0 if get_token() else 1)' 2>/dev/null; then
  ok "Hugging Face token available (voice backend can authenticate)"
else
  bad "no Hugging Face token - the voice backend will not start"
  fix "\"$VENV/bin/hf\" auth login"
fi

# Audio input. The app records silence rather than erroring when the microphone
# is denied or the wrong device is default, so Reachy simply never responds and
# nothing in its log says why. Check the device and the actual signal level.
echo
echo "Audio input"
DEFAULT_IN=$(system_profiler SPAudioDataType 2>/dev/null \
  | awk '/^ {8}[A-Za-z].*:$/{d=$0} /Default Input Device: Yes/{gsub(/^ +| *:$/,"",d); print d; exit}')
if [ -n "$DEFAULT_IN" ]; then ok "default input device: $DEFAULT_IN"
else note "could not determine the default input device"; fi

LEVEL=$("$VENV/bin/python" - <<'PYEOF' 2>/dev/null
import gi, os, pathlib, time
try:
    import gstreamer_python
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    os.environ.setdefault("GST_PLUGIN_PATH",
        str(pathlib.Path(gstreamer_python.__file__).parent / "lib/gstreamer-1.0"))
    Gst.init(None)
    p = Gst.parse_launch("osxaudiosrc num-buffers=60 ! audioconvert ! "
                         "audio/x-raw,format=S16LE,channels=1 ! "
                         "level interval=500000000 ! fakesink sync=false")
    bus = p.get_bus(); p.set_state(Gst.State.PLAYING)
    peaks, end = [], time.time() + 5
    while time.time() < end:
        m = bus.timed_pop_filtered(500 * Gst.MSECOND,
                                   Gst.MessageType.ELEMENT | Gst.MessageType.EOS)
        if not m: continue
        if m.type == Gst.MessageType.EOS: break
        st = m.get_structure()
        if st and st.get_name() == "level": peaks.append(st.get_value("peak")[0])
    p.set_state(Gst.State.NULL)
    print(round(max(peaks), 1) if peaks else "none")
except Exception:
    print("unavailable")
PYEOF
)
case "$LEVEL" in
  none|unavailable|"") note "could not measure the input level (audio check skipped)" ;;
  *) if [ "${LEVEL%%.*}" -lt -100 ] 2>/dev/null; then
       note "microphone is SILENT (peak ${LEVEL} dB) - Reachy will never hear you"
       fix "grant your terminal app Microphone access in System Settings > Privacy & Security"
       fix "and confirm the right device is selected in System Settings > Sound > Input"
     else
       ok "microphone is capturing audio (peak ${LEVEL} dB)"
     fi ;;
esac

[ -f "$APP/.env" ] && ok ".env present" || note "no .env (not required yet)"

echo
echo "---------------------------------------------"
printf 'passed %d   warnings %d   failed %d\n' "$pass" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then
  echo "Environment is NOT ready. Fix the FAIL lines above."
  exit 1
fi
echo "Environment is ready."
echo
echo "Run the app with:"
echo "  cd \"$APP\""
echo "  \"$VENV/bin/python\" -m reachy_language_tutor.main --ui --no-camera"
