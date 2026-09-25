"""Tests for persisted instance-local startup settings."""

from reachy_language_tutor.startup_settings import (
    StartupSettings,
    read_startup_settings,
    write_startup_settings,
    load_startup_settings_into_runtime,
)

from profile_lock import REQUIRES_PROFILE_SWITCHING


def test_write_and_read_startup_settings(tmp_path) -> None:
    """Startup settings should round-trip through startup_settings.json."""
    write_startup_settings(tmp_path, profile="sorry_bro", voice="shimmer")

    assert read_startup_settings(tmp_path) == StartupSettings(profile="sorry_bro", voice="shimmer")


@REQUIRES_PROFILE_SWITCHING
def test_load_startup_settings_into_runtime_applies_profile_when_no_env(monkeypatch, tmp_path) -> None:
    """Startup settings should seed the runtime profile when no explicit env override exists."""
    write_startup_settings(tmp_path, profile="sorry_bro", voice="shimmer")
    applied_profiles: list[str | None] = []
    monkeypatch.delenv("REACHY_MINI_CUSTOM_PROFILE", raising=False)
    monkeypatch.setattr(
        "reachy_language_tutor.config.set_custom_profile",
        lambda profile: applied_profiles.append(profile),
    )

    settings = load_startup_settings_into_runtime(tmp_path)

    assert settings == StartupSettings(profile="sorry_bro", voice="shimmer")
    assert applied_profiles == ["sorry_bro"]


@REQUIRES_PROFILE_SWITCHING
def test_load_startup_settings_into_runtime_saved_settings_override_instance_env(monkeypatch, tmp_path) -> None:
    """Saved startup settings should override an instance-local profile env value."""
    write_startup_settings(tmp_path, profile="sorry_bro", voice="shimmer")
    applied_profiles: list[str | None] = []
    monkeypatch.setenv("REACHY_MINI_CUSTOM_PROFILE", "env_profile")
    monkeypatch.setattr(
        "reachy_language_tutor.config.set_custom_profile",
        lambda profile: applied_profiles.append(profile),
    )

    settings = load_startup_settings_into_runtime(tmp_path)

    assert settings == StartupSettings(profile="sorry_bro", voice="shimmer")
    assert applied_profiles == ["sorry_bro"]


@REQUIRES_PROFILE_SWITCHING
def test_load_startup_settings_into_runtime_saved_settings_override_inherited_env(monkeypatch, tmp_path) -> None:
    """Saved startup settings should override a profile inherited from another `.env`."""
    write_startup_settings(tmp_path, profile="nature_documentarian", voice="cedar")
    applied_profiles: list[str | None] = []
    monkeypatch.setenv("REACHY_MINI_CUSTOM_PROFILE", "env_profile")
    monkeypatch.setattr(
        "reachy_language_tutor.config.set_custom_profile",
        lambda profile: applied_profiles.append(profile),
    )

    settings = load_startup_settings_into_runtime(tmp_path)

    assert settings == StartupSettings(profile="nature_documentarian", voice="cedar")
    assert applied_profiles == ["nature_documentarian"]


def test_load_startup_settings_into_runtime_preserves_inherited_env_without_saved_settings(
    monkeypatch, tmp_path
) -> None:
    """Inherited env config should still apply when no startup settings have been saved."""
    applied_profiles: list[str | None] = []
    monkeypatch.setenv("REACHY_MINI_CUSTOM_PROFILE", "env_profile")
    monkeypatch.setattr(
        "reachy_language_tutor.config.set_custom_profile",
        lambda profile: applied_profiles.append(profile),
    )

    settings = load_startup_settings_into_runtime(tmp_path)

    assert settings == StartupSettings()
    assert applied_profiles == []


def test_an_unreadable_settings_file_logs_neither_its_path_nor_its_content(tmp_path, caplog) -> None:
    """The settings file can hold a learner id, in an instance directory that can name a household.

    Measured before: a bare JSON list put `['<learner id>']` into a WARNING, and a truncated
    file put the full path under the instance directory there.
    """
    import logging

    household = tmp_path / "smith-household"
    household.mkdir()
    learner_id = "3f1c2a9e-7d41-4b8e-9c1a-0e5d2f6b8a77"
    settings = household / "startup_settings.json"
    shapes = (f'["{learner_id}"]', f'"{learner_id}"', '{"fallback_learner": "' + learner_id + '",')

    for content in shapes:
        settings.write_text(content, encoding="utf-8")
        with caplog.at_level(logging.DEBUG, logger="reachy_language_tutor.startup_settings"):
            assert read_startup_settings(household) == StartupSettings()

    assert len(caplog.records) == len(shapes), "each malformed shape must still be reported"
    surface = " ".join(f"{record.getMessage()} {record.args}" for record in caplog.records)
    assert learner_id not in surface, "a learner id reached the log"
    assert "smith-household" not in surface, "the instance path reached the log"
