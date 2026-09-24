"""Guard the camera-leak patch's backup against outliving the file it backs up.

scripts/patch_daemon_camera_leak.py edits the desktop app's installed media_server.py and
keeps a .orig beside it. Upgrading reachy-mini rewrites media_server.py but leaves the
.orig, because pip only removes files it installed -- the 1.10.0 -> 1.11.0 upgrade did
exactly that. The script used to keep any existing backup, so re-patching after the
upgrade kept the 1.10.0 file as the "original", and --revert then put that 1.10.0 file
into a 1.11.0 install.

These tests run the script's functions against synthetic files in tmp_path. They never
touch the real daemon venv.
"""

import importlib.util
from types import ModuleType
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "patch_daemon_camera_leak.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("patch_daemon_camera_leak", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patch = _load()


def _shipped(version: str) -> str:
    """Return a stand-in media_server.py: the real anchor, and a line only this version has."""
    return f"# reachy-mini {version}\ndef build(pipeline, Gst):\n{patch.ANCHOR}        return queue_webrtc\n"


@pytest.fixture
def upgraded_install(tmp_path: Path) -> Path:
    """Build a 1.11.0 media_server.py beside the 1.10.0 backup an upgrade left behind."""
    path = tmp_path / "media_server.py"
    path.write_text(_shipped("1.11.0"), encoding="utf-8")
    patch.backup_path(path).write_text(_shipped("1.10.0"), encoding="utf-8")
    return path


def test_patching_over_a_stale_backup_replaces_it_with_the_current_original(upgraded_install: Path) -> None:
    """Re-patching after an upgrade must not keep the old version as the original."""
    assert patch.apply(upgraded_install) == 0
    assert patch.backup_path(upgraded_install).read_text(encoding="utf-8") == _shipped("1.11.0")


def test_revert_after_an_upgrade_restores_this_version_not_the_last_one(upgraded_install: Path) -> None:
    """The defect itself: --revert put a 1.10.0 file into a 1.11.0 install."""
    patch.apply(upgraded_install)
    assert patch.revert(upgraded_install) == 0
    assert upgraded_install.read_text(encoding="utf-8") == _shipped("1.11.0")


def test_revert_refuses_a_backup_that_is_not_this_files_original(tmp_path: Path) -> None:
    """The state the old script could leave behind: patched, with a stale backup."""
    path = tmp_path / "media_server.py"
    patched_111 = patch.patched(_shipped("1.11.0"))
    path.write_text(patched_111, encoding="utf-8")
    patch.backup_path(path).write_text(_shipped("1.10.0"), encoding="utf-8")

    assert patch.revert(path) == 4
    assert path.read_text(encoding="utf-8") == patched_111


def test_check_warns_when_a_patched_files_backup_is_stale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--check is where a stale backup is noticed before anyone relies on it."""
    path = tmp_path / "media_server.py"
    path.write_text(patch.patched(_shipped("1.11.0")), encoding="utf-8")
    patch.backup_path(path).write_text(_shipped("1.10.0"), encoding="utf-8")

    assert patch.report(path) == 0
    assert "--revert will refuse it" in capsys.readouterr().out


def test_a_fresh_install_round_trips_byte_for_byte(tmp_path: Path) -> None:
    """The ordinary case the backup check must not break."""
    path = tmp_path / "media_server.py"
    path.write_text(_shipped("1.11.0"), encoding="utf-8")

    assert patch.apply(path) == 0
    assert patch.MARKER in path.read_text(encoding="utf-8")
    assert patch.backup_matches(path)
    assert patch.revert(path) == 0
    assert path.read_text(encoding="utf-8") == _shipped("1.11.0")


def test_revert_leaves_an_unpatched_file_alone_even_with_a_stale_backup(upgraded_install: Path) -> None:
    """The state right after an upgrade: nothing to revert, so nothing may be copied."""
    assert patch.revert(upgraded_install) == 0
    assert upgraded_install.read_text(encoding="utf-8") == _shipped("1.11.0")
