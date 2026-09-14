#!/usr/bin/env bash
#
# Remove this app from the Reachy Mini simulator.
#
#     ./scripts/remove-from-simulator.sh
#
# The undo for ./scripts/install-into-simulator.sh. It uninstalls the package from the
# desktop app's apps environment, which is what makes the app disappear from the
# dashboard -- apps are discovered through the `reachy_mini_apps` entry point, so
# removing the package removes the app.
#
# It does NOT touch ~/dev/reachy/reachy_mini_env, so your editable development install
# and the test suite are unaffected, and it does not touch the daemon's own .venv.
set -euo pipefail

APP_SUPPORT="${REACHY_APP_SUPPORT:-$HOME/Library/Application Support/com.pollen-robotics.reachy-mini}"
DAEMON="${REACHY_DAEMON:-http://localhost:8000}"
APP_NAME="reachy_language_tutor"
DIST_NAME="reachy-language-tutor"   # the distribution name; underscores become hyphens

say()  { printf '%s\n' "$*"; }
fail() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

APPS_PYTHON="$APP_SUPPORT/apps_venv/bin/python3"
[ -x "$APP_SUPPORT/uv" ] || fail "the desktop app's uv is not at $APP_SUPPORT/uv"
[ -x "$APPS_PYTHON" ] || fail "no apps environment at $APPS_PYTHON"

registered() {
  "$APPS_PYTHON" -c "
import importlib.metadata as md, sys
names = {e.name for e in md.entry_points().select(group='reachy_mini_apps')}
sys.exit(0 if '$APP_NAME' in names else 1)
"
}

if ! registered; then
  say "$APP_NAME is not installed in the simulator; nothing to do."
  exit 0
fi

# If it is the app currently running, stop it first rather than pulling the floor out
# from under a live session. The daemon may not be up, which is fine.
if curl -s --max-time 5 "$DAEMON/api/daemon/status" >/dev/null 2>&1; then
  if curl -s --max-time 10 "$DAEMON/api/apps/current-app-status" 2>/dev/null | grep -q "$APP_NAME"; then
    say "it is the running app; stopping it first"
    curl -s --max-time 30 -X POST "$DAEMON/api/apps/stop-current-app" >/dev/null || true
  fi
fi

say "uninstalling $DIST_NAME from the apps environment..."
"$APP_SUPPORT/uv" pip uninstall --python "$APPS_PYTHON" "$DIST_NAME" || fail "the uninstall failed; the output above says why"

# Verify by asking, not by trusting the uninstaller.
if registered; then
  fail "$APP_NAME still registers a reachy_mini_apps entry point."
fi
say "removed: $APP_NAME no longer registers an entry point"

if curl -s --max-time 5 "$DAEMON/api/daemon/status" >/dev/null 2>&1; then
  if curl -s --max-time 10 "$DAEMON/api/apps/list-available/installed" | tr ',' '\n' | grep -q "\"$APP_NAME\""; then
    say "NOTE: the daemon still lists it. Relaunch the desktop app to refresh:"
    say "        open -a 'Reachy Mini Control'"
  else
    say "the daemon no longer lists it"
  fi
fi

say ""
say "Your development install is untouched: ~/dev/reachy/reachy_mini_env still has the"
say "editable package, and the test suite still runs."
