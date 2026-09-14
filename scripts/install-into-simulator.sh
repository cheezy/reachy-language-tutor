#!/usr/bin/env bash
#
# Install this app into the Reachy Mini simulator so it appears in the desktop
# dashboard and can be started like any other app.
#
#     ./scripts/install-into-simulator.sh
#     ./scripts/install-into-simulator.sh --start    # and launch it afterwards
#
# Undo with ./scripts/remove-from-simulator.sh
#
# WHICH ENVIRONMENT, AND WHY IT IS NOT THE OBVIOUS ONE
# ----------------------------------------------------
# There are THREE Python environments on this machine and installing into the wrong
# one is the version-skew trap docs/SETUP.md section 5 is about:
#
#   ~/dev/reachy/reachy_mini_env                     your SDK; where the tests run
#   <app support>/.venv                              the DAEMON's own environment
#   <app support>/apps_venv                          where reachy mini APPS live
#
# Apps are discovered through the `reachy_mini_apps` entry point in apps_venv, which
# is where this installs, using the desktop app's OWN bundled `uv` so the resolver is
# the same one the app uses for itself.
#
# NOT through the daemon's /api/apps/install: that endpoint answers
# "source_kind 'local' is not installable via the API" for a local directory. It is
# for Hugging Face spaces. This was established by trying it.
#
# The install is EDITABLE (-e), so your working tree is what the simulator runs and
# you do not have to reinstall after every change -- only after changing entry points
# or dependencies.
set -euo pipefail

APP_SUPPORT="${REACHY_APP_SUPPORT:-$HOME/Library/Application Support/com.pollen-robotics.reachy-mini}"
DAEMON="${REACHY_DAEMON:-http://localhost:8000}"
APP_NAME="reachy_language_tutor"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$REPO_ROOT/$APP_NAME"
START_AFTER=0
[ "${1:-}" = "--start" ] && START_AFTER=1

say()  { printf '%s\n' "$*"; }
fail() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

# --- what we are installing, and what we are installing into --------------------
[ -f "$APP_DIR/pyproject.toml" ] || fail "no pyproject.toml under $APP_DIR"
[ -x "$APP_SUPPORT/uv" ] || fail "the desktop app's uv is not at $APP_SUPPORT/uv.
  Is Reachy Mini Control installed? Set REACHY_APP_SUPPORT if it lives elsewhere."
APPS_PYTHON="$APP_SUPPORT/apps_venv/bin/python3"
[ -x "$APPS_PYTHON" ] || fail "no apps environment at $APPS_PYTHON.
  Launch the desktop app once so it provisions one:  open -a 'Reachy Mini Control'"

say "app : $APP_DIR"
say "into: $APPS_PYTHON"

# --- the SDK versions must stay in step -----------------------------------------
# This app declares reachy-mini>=1.10.0rc5, so the install CAN move the SDK in
# apps_venv and put it out of step with the daemon. Captured before and compared
# after, because docs/SETUP.md records this as the thing that silently breaks motion.
sdk_of() { "$1" -c 'import reachy_mini; print(reachy_mini.__version__)' 2>/dev/null || echo "unknown"; }
before="$(sdk_of "$APPS_PYTHON")"
daemon_sdk="$(sdk_of "$APP_SUPPORT/.venv/bin/python3")"
say "reachy_mini: apps_venv $before, daemon $daemon_sdk"

# --- install ---------------------------------------------------------------------
say "installing (editable)..."
"$APP_SUPPORT/uv" pip install --python "$APPS_PYTHON" -e "$APP_DIR" || fail "the install itself failed; the output above says why"

after="$(sdk_of "$APPS_PYTHON")"
if [ "$before" != "$after" ]; then
  say ""
  say "WARNING: installing moved reachy_mini in apps_venv from $before to $after."
  say "  The daemon is on $daemon_sdk. If those now differ, motion and audio can fail in"
  say "  ways that look unrelated. docs/SETUP.md section 5 has the fix."
fi

# --- verify by asking, not by trusting the installer -----------------------------
"$APPS_PYTHON" -c "
import importlib.metadata as md, sys
names = {e.name for e in md.entry_points().select(group='reachy_mini_apps')}
sys.exit(0 if '$APP_NAME' in names else 1)
" || fail "$APP_NAME did not register a reachy_mini_apps entry point.
  Check [project.entry-points.reachy_mini_apps] in $APP_DIR/pyproject.toml"
say "entry point registered: $APP_NAME"

# The daemon is the thing that actually lists apps to you, so ask it too. It may not
# be running, and that is not a failure of the install.
if curl -s --max-time 5 "$DAEMON/api/daemon/status" >/dev/null 2>&1; then
  if curl -s --max-time 10 "$DAEMON/api/apps/list-available/installed" | tr ',' '\n' | grep -q "\"$APP_NAME\""; then
    say "the daemon lists it as installed"
  else
    say "NOTE: the daemon is running but does not list it yet. Relaunch the desktop app:"
    say "        open -a 'Reachy Mini Control'"
  fi
  if [ "$START_AFTER" = "1" ]; then
    say "starting..."
    curl -s --max-time 60 -X POST "$DAEMON/api/apps/start-app/$APP_NAME" | head -c 300; echo
  fi
else
  say "NOTE: the daemon is not running, so nothing could be verified through it."
  say "      Open the desktop app to see the installed app:  open -a 'Reachy Mini Control'"
fi

say ""
say "Done. Remove it again with:  ./scripts/remove-from-simulator.sh"
say "Your ~/dev/reachy/reachy_mini_env install and the test suite are untouched."
