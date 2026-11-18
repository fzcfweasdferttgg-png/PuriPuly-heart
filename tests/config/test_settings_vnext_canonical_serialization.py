from __future__ import annotations

import json
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from puripuly_heart.config.settings_vnext.schema import (
    VNEXT_SETTINGS_SCHEMA_VERSION,
    AppSettingsVNext,
    CaptureTargetIntent,
    ProcessCaptureTargetIntent,
    with_capture_target,
)
from tests.config.settings_migration_fixtures import (
    maximal_v24_settings_fixture,
)


def _load_module(name: str) -> ModuleType:
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"{name} should import: {exc}")


def _migration() -> ModuleType:
    return _load_module("puripuly_heart.config.settings_vnext.migration")


def _serialization() -> ModuleType:
    return _load_module("puripuly_heart.config.settings_vnext.serialization")


def _compat() -> ModuleType:
    return _load_module("puripuly_heart.config.settings_vnext.compat")


def _write_json_bytes(path: Path, data: dict[str, Any]) -> bytes:
    raw_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw_bytes)
    return raw_bytes


def test_main_input_first_launch_flag_survives_reload_and_defaults_off(
    tmp_path: Path,
) -> None:
    current = _serialization().to_dict(AppSettingsVNext())
    current["state"].pop("main_input_hint")
    legacy_path = tmp_path / "legacy.json"
    _write_json_bytes(legacy_path, current)

    legacy = _compat().load_vnext_settings(legacy_path)

    assert legacy.ok
    assert legacy.settings.state.main_input_hint.first_launch_completed is False

    completed = _serialization().to_dict(AppSettingsVNext())
    completed["state"]["main_input_hint"]["first_launch_completed"] = True
    completed_path = tmp_path / "completed.json"
    _write_json_bytes(completed_path, completed)

    reloaded = _compat().load_vnext_settings(completed_path)

    assert reloaded.ok
    assert reloaded.settings.state.main_input_hint.first_launch_completed is True


@pytest.mark.parametrize("settings_version", [25, VNEXT_SETTINGS_SCHEMA_VERSION + 100])
def test_canonical_migration_loader_rejects_flat_shape(settings_version: int) -> None:
    migration = _migration()
    raw = maximal_v24_settings_fixture()
    raw["settings_version"] = settings_version

    with pytest.raises(ValueError, match="canonical settings"):
        migration.from_dict(raw)


def test_raw_vad_threshold_persists_without_unrelated_loss(
    tmp_path: Path,
) -> None:
    compat = _compat()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["intent"]["desktop_audio"]["vad_speech_threshold"] = 0.05
    raw["intent"]["stt"]["vad_speech_threshold"] = 0.06
    raw["intent"]["ui"]["locale"] = "ja"
    path = tmp_path / "settings.json"
    original_bytes = _write_json_bytes(path, raw)

    first = compat.load_vnext_settings(path)

    assert first.ok
    assert first.settings is not None
    assert first.settings.intent.desktop_audio.vad_speech_threshold == 0.05
    assert first.settings.intent.stt.vad_speech_threshold == 0.06
    assert first.settings.intent.ui.locale == "ja"
    assert path.read_bytes() == original_bytes

    save_result = compat.save_vnext_settings(path, first.settings)
    assert save_result.ok
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["intent"]["desktop_audio"]["vad_speech_threshold"] == 0.05
    assert persisted["intent"]["stt"]["vad_speech_threshold"] == 0.06
    assert persisted["intent"]["ui"]["locale"] == "ja"

    second = compat.load_vnext_settings(path)
    assert second.ok


def test_restart_roundtrip_preserves_gemma_main_model_with_stale_openrouter_url() -> None:
    from puripuly_heart.config.settings_vnext import migration, serialization

    canonical = serialization.to_dict(AppSettingsVNext())
    translation = canonical["intent"]["translation"]
    translation["model"] = "gemma4_26b_31b"
    translation["connection"] = "managed"
    translation["openrouter_model"] = "deepseek/deepseek-v4-flash"
    translation["openrouter_selected_source"] = "managed"
    translation["openrouter_selection_alias"] = "gemma4_26b_31b_managed"

    result = serialization.to_dict(migration.from_dict(canonical))["intent"]["translation"]

    assert result["model"] == "gemma4_26b_31b"
    assert result["connection"] == "managed"


def test_current_vnext_dict_reads_and_serializes_idempotently() -> None:
    migration = _migration()
    serialization = _serialization()

    original = AppSettingsVNext()
    raw = serialization.to_dict(original)

    loaded = migration.from_dict(raw)
    serialized = serialization.to_dict(loaded)

    assert loaded == original
    assert serialized == raw


def test_canonical_settings_version_must_be_a_supported_integer() -> None:
    migration = _migration()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["settings_version"] = "not-a-schema-discriminator"

    with pytest.raises(ValueError, match="positive integer"):
        migration.from_dict(raw)

    raw["settings_version"] = VNEXT_SETTINGS_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="unsupported canonical"):
        migration.from_dict(raw)


def test_generic_process_identity_is_normalized_without_relocation() -> None:
    serialization = _serialization()
    migration = _migration()
    original_identity = r"C:/Apps/Example/Example.EXE"
    target = CaptureTargetIntent.process_target(
        ProcessCaptureTargetIntent.generic_executable(original_identity)
    )
    settings = with_capture_target(AppSettingsVNext(), target)

    restored = migration.from_dict(serialization.to_dict(settings))

    assert restored.intent.desktop_audio.capture_target.process is not None
    assert restored.intent.desktop_audio.capture_target.process.executable_identity == (
        r"c:\apps\example\example.exe"
    )
    assert (
        restored.intent.desktop_audio.capture_target.process.executable_identity
        != original_identity
    )


@pytest.mark.parametrize(
    "process",
    [
        ProcessCaptureTargetIntent.generic_executable(r"\\server\share\example.exe"),
        ProcessCaptureTargetIntent.vrchat(r"\\server\share\VRChat.exe"),
    ],
)
def test_persisted_process_targets_accept_fully_qualified_unc_identities(
    process: ProcessCaptureTargetIntent,
) -> None:
    serialization = _serialization()
    migration = _migration()
    settings = AppSettingsVNext(
        intent=replace(
            AppSettingsVNext().intent,
            desktop_audio=replace(
                AppSettingsVNext().intent.desktop_audio,
                capture_target=CaptureTargetIntent.process_target(process),
            ),
        )
    )

    restored = migration.from_dict(serialization.to_dict(settings))

    assert restored.intent.desktop_audio.capture_target.process == process


@pytest.mark.parametrize(
    ("process_kind", "identity", "accepted"),
    [
        ("generic_executable", r"C:\Apps\example.exe", True),
        ("generic_executable", r"\\server\share\example.exe", True),
        ("generic_executable", r"C:example.exe", False),
        ("generic_executable", r"\??\C:\example.exe", False),
        ("generic_executable", r"\Device\Audio\example.exe", False),
        ("vrchat", r"C:\Apps\VRChat.exe", True),
        ("vrchat", r"\\server\share\VRChat.exe", True),
        ("vrchat", r"C:VRChat.exe", False),
        ("vrchat", r"\??\C:\VRChat.exe", False),
        ("vrchat", r"\Device\Audio\VRChat.exe", False),
    ],
)
def test_persisted_process_identity_path_matrix(
    process_kind: str,
    identity: str,
    accepted: bool,
) -> None:
    factory = getattr(ProcessCaptureTargetIntent, process_kind)

    if accepted:
        assert factory(identity).executable_identity is not None
        return
    with pytest.raises(ValueError, match="executable"):
        factory(identity)


@pytest.mark.parametrize(
    ("process_kind", "identity", "accepted"),
    [
        ("generic_executable", r"c:\apps\example.exe", True),
        ("generic_executable", r"\\server\share\example.exe", True),
        ("generic_executable", r"c:example.exe", False),
        ("generic_executable", r"\??\c:\example.exe", False),
        ("generic_executable", r"\device\audio\example.exe", False),
        ("vrchat", r"c:\apps\vrchat.exe", True),
        ("vrchat", r"\\server\share\vrchat.exe", True),
        ("vrchat", r"c:vrchat.exe", False),
        ("vrchat", r"\??\c:\vrchat.exe", False),
        ("vrchat", r"\device\audio\vrchat.exe", False),
    ],
)
def test_resolved_process_identity_path_matrix(
    process_kind: str,
    identity: str,
    accepted: bool,
) -> None:
    from puripuly_heart.config.resolved import ResolvedDesktopAudioCaptureTarget

    kwargs = {
        "kind": "process",
        "process_kind": process_kind,
        "executable_identity": identity,
    }
    if accepted:
        assert ResolvedDesktopAudioCaptureTarget(**kwargs).executable_identity == identity
        return
    with pytest.raises(ValueError, match="executable identity"):
        ResolvedDesktopAudioCaptureTarget(**kwargs)


def test_capture_target_validation_rejects_ambiguous_or_path_bound_discord_values() -> None:
    with pytest.raises(ValueError, match="non-empty device name"):
        CaptureTargetIntent.named_output_device("   ")
    with pytest.raises(ValueError, match="VRChat.exe"):
        ProcessCaptureTargetIntent.vrchat(r"C:\Apps\Other.exe")
    with pytest.raises(ValueError, match="installation path"):
        ProcessCaptureTargetIntent(
            kind="discord",
            executable_identity=r"C:\Users\example\AppData\Discord\app-1.0\Discord.exe",
            discord_channel="stable",
        )
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.generic_executable("Example.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.vrchat(r"\VRChat\VRChat.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.generic_executable(r"\\server\example.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.vrchat(r"\\server\VRChat.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.generic_executable(r"\\.\pipe\capture.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.vrchat(r"\\.\pipe\VRChat.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.generic_executable(r"\\?\c:\capture.exe")
    with pytest.raises(ValueError, match="executable"):
        ProcessCaptureTargetIntent.vrchat(r"\\?\c:\VRChat.exe")
    with pytest.raises(ValueError, match="Discord channel identity"):
        ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Discord\Discord.exe")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "process", "process_kind": "generic_executable"},
        {
            "kind": "process",
            "process_kind": "generic_executable",
            "executable_identity": r"C:\Apps\Discord\Discord.exe",
        },
        {
            "kind": "process",
            "process_kind": "generic_executable",
            "executable_identity": "example.exe",
        },
        {
            "kind": "process",
            "process_kind": "vrchat",
            "executable_identity": r"C:\Apps\Other.exe",
        },
        {
            "kind": "process",
            "process_kind": "vrchat",
            "executable_identity": r"\VRChat\VRChat.exe",
        },
        {
            "kind": "process",
            "process_kind": "generic_executable",
            "executable_identity": r"\\server\example.exe",
        },
        {
            "kind": "process",
            "process_kind": "vrchat",
            "executable_identity": r"\\server\vrchat.exe",
        },
        {
            "kind": "process",
            "process_kind": "generic_executable",
            "executable_identity": r"\\.\pipe\capture.exe",
        },
        {
            "kind": "process",
            "process_kind": "vrchat",
            "executable_identity": r"\\.\pipe\vrchat.exe",
        },
        {
            "kind": "process",
            "process_kind": "generic_executable",
            "executable_identity": r"\\?\c:\capture.exe",
        },
        {
            "kind": "process",
            "process_kind": "vrchat",
            "executable_identity": r"\\?\c:\vrchat.exe",
        },
        {
            "kind": "process",
            "process_kind": "discord",
            "discord_channel": "stable",
            "executable_basename": "DiscordPTB.exe",
        },
        {
            "kind": "process",
            "process_kind": "discord",
            "executable_identity": r"C:\Apps\Discord\Discord.exe",
            "discord_channel": "stable",
            "executable_basename": "Discord.exe",
        },
        {
            "kind": "process",
            "process_kind": "discord",
            "discord_channel": "Stable",
            "executable_basename": "Discord.exe",
        },
    ],
)
def test_resolved_process_capture_target_rejects_incomplete_or_malformed_values(
    kwargs: dict[str, object],
) -> None:
    from puripuly_heart.config.resolved import ResolvedDesktopAudioCaptureTarget

    with pytest.raises(ValueError):
        ResolvedDesktopAudioCaptureTarget(**kwargs)


def test_resolved_process_capture_target_accepts_canonical_generic_vrchat_and_discord_values() -> (
    None
):
    from puripuly_heart.config.resolved import ResolvedDesktopAudioCaptureTarget

    generic = ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind="generic_executable",
        executable_identity=r"c:\apps\example\example.exe",
    )
    vrchat = ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind="vrchat",
        executable_identity=r"c:\vrchat\vrchat.exe",
    )
    discord = ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind="discord",
        discord_channel="canary",
        executable_basename="DiscordCanary.exe",
    )
    generic_unc = ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind="generic_executable",
        executable_identity=r"\\server\share\example.exe",
    )
    vrchat_unc = ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind="vrchat",
        executable_identity=r"\\server\share\vrchat.exe",
    )

    assert generic.process_kind == "generic_executable"
    assert vrchat.process_kind == "vrchat"
    assert discord.discord_channel == "canary"
    assert generic_unc.executable_identity == r"\\server\share\example.exe"
    assert vrchat_unc.executable_identity == r"\\server\share\vrchat.exe"


def test_capture_target_mutation_updates_only_immutable_vnext_intent() -> None:
    original = AppSettingsVNext()
    target = CaptureTargetIntent.process_target(
        ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Example\Example.exe")
    )

    updated = with_capture_target(original, target)

    assert updated is not original
    assert original.intent.desktop_audio.capture_target.kind == "default_output_device"
    assert updated.intent.desktop_audio.capture_target == target
    assert updated.state == original.state


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (
            CaptureTargetIntent.default_output_device(),
            {"kind": "default_output_device", "device_name": None},
        ),
        (
            CaptureTargetIntent.named_output_device("Fixture Speakers"),
            {"kind": "named_output_device", "device_name": "Fixture Speakers"},
        ),
        (
            CaptureTargetIntent.process_target(
                ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\example.exe")
            ),
            {
                "kind": "process",
                "process_kind": "generic_executable",
                "executable_identity": r"c:\apps\example.exe",
            },
        ),
        (
            CaptureTargetIntent.process_target(
                ProcessCaptureTargetIntent.vrchat(r"C:\Apps\VRChat.exe")
            ),
            {
                "kind": "process",
                "process_kind": "vrchat",
                "executable_identity": r"c:\apps\vrchat.exe",
            },
        ),
        (
            CaptureTargetIntent.process_target(ProcessCaptureTargetIntent.discord("PTB")),
            {
                "kind": "process",
                "process_kind": "discord",
                "discord_channel": "ptb",
                "executable_basename": "DiscordPTB.exe",
            },
        ),
    ],
)
def test_capture_target_resolution_covers_all_target_kinds(
    target: CaptureTargetIntent,
    expected: dict[str, str | None],
) -> None:
    from puripuly_heart.config.capture_target_resolution import resolve_desktop_audio_capture_target

    resolved = resolve_desktop_audio_capture_target(target)

    for field, value in expected.items():
        assert getattr(resolved, field) == value


def test_capture_target_resolution_excludes_pids_and_runtime_state() -> None:
    from puripuly_heart.config.capture_target_resolution import resolve_desktop_audio_capture_target

    target = CaptureTargetIntent.process_target(
        ProcessCaptureTargetIntent.vrchat(r"C:\VRChat\VRChat.exe")
    )

    resolved = resolve_desktop_audio_capture_target(target)
    serialized = _serialization().to_dict(
        AppSettingsVNext(
            intent=replace(
                AppSettingsVNext().intent,
                desktop_audio=replace(
                    AppSettingsVNext().intent.desktop_audio, capture_target=target
                ),
            )
        )
    )

    assert resolved.kind == "process"
    assert resolved.process_kind == "vrchat"
    assert resolved.executable_identity == r"c:\vrchat\vrchat.exe"
    assert not {"pid", "active", "warning", "retry", "capture_state"}.intersection(
        serialized["intent"]["desktop_audio"]["capture_target"]
    )


def test_save_vnext_settings_normalizes_stale_settings_version_to_current(
    tmp_path: Path,
) -> None:
    compat = _compat()
    path = tmp_path / "settings.json"
    stale = replace(AppSettingsVNext(), settings_version=VNEXT_SETTINGS_SCHEMA_VERSION - 1)

    result = compat.save_vnext_settings(path, stale)

    assert result.ok
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"settings_version", "intent", "state"}
    assert raw["settings_version"] == VNEXT_SETTINGS_SCHEMA_VERSION


def test_save_uses_unique_tempfile_and_ignores_another_writer_stale_temp_name(
    tmp_path: Path,
) -> None:
    compat = _compat()
    path = tmp_path / "settings.json"
    stale_temp_path = tmp_path / "settings.json.tmp"
    stale_temp_path.mkdir()

    result = compat.save_vnext_settings(path, AppSettingsVNext())

    assert result.ok
    assert path.is_file()
    assert stale_temp_path.is_dir()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".settings.json.")]
    assert leftovers == []
    assert not list(tmp_path.glob("*.bak"))


def test_parse_and_migration_failures_return_explicit_results_without_overwrite(
    tmp_path: Path,
) -> None:
    compat = _compat()
    parse_path = tmp_path / "parse-settings.json"
    parse_path.write_text("not json", encoding="utf-8")

    parse_result = compat.load_vnext_settings(parse_path)

    assert parse_result.status == compat.SettingsPersistenceStatus.PARSE_FAILED
    assert parse_path.read_text(encoding="utf-8") == "not json"

    migration_path = tmp_path / "migration-settings.json"
    migration_bytes = _write_json_bytes(
        migration_path,
        {
            "settings_version": VNEXT_SETTINGS_SCHEMA_VERSION,
            "intent": {},
            "state": {"provider_verification": []},
        },
    )

    migration_result = compat.load_vnext_settings(migration_path)
    assert migration_result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert migration_result.error is not None
    assert migration_result.error.message == "migration_failed:ValueError"
    assert "unsupported_anchor" not in migration_result.error.message
    assert migration_path.read_bytes() == migration_bytes


@pytest.mark.parametrize("original_bytes", [b"[]", b"null", b'"text"'])
def test_top_level_non_object_json_fails_without_backup_or_overwrite(
    tmp_path: Path,
    original_bytes: bytes,
) -> None:
    compat = _compat()
    path = tmp_path / "settings.json"
    path.write_bytes(original_bytes)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.PARSE_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    [
        (("intent", "ui", "locale"), []),
        (("intent", "osc", "chatbox_send"), "true"),
        (("state", "github_star_prompt", "clicked"), 1),
        (("intent", "translation"), []),
        (("intent", "desktop_audio"), []),
        (("intent", "prompts"), []),
        (("intent", "translation", "concurrency_limit"), "5"),
        (("intent", "languages", "peer_expected_languages"), [123]),
        (("intent", "languages", "recent_source_languages"), [123]),
        (("intent", "translation", "connection_history"), {"gemma4_26b_31b": 123}),
        (("intent", "stt", "custom_terms"), {"ko": [123]}),
    ],
)
def test_malformed_canonical_nested_value_fails_without_backup_or_overwrite(
    tmp_path: Path,
    field_path: tuple[str, ...],
    invalid_value: object,
) -> None:
    compat = _compat()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    target = raw
    for segment in field_path[:-1]:
        target = target[segment]
    target[field_path[-1]] = invalid_value
    path = tmp_path / "settings.json"
    original_bytes = _write_json_bytes(path, raw)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("source_shape", ["flat", "canonical"])
def test_nonstandard_json_constants_follow_shape_specific_cutover_policy(
    tmp_path: Path,
    constant: str,
    source_shape: str,
) -> None:
    compat = _compat()
    path = tmp_path / f"{source_shape}-{constant}.json"
    if source_shape == "canonical":
        raw_text = (
            f'{{"settings_version": {VNEXT_SETTINGS_SCHEMA_VERSION}, '
            f'"intent": {{"ui": {{"locale": {constant}}}}}, "state": {{}}}}'
        )
    else:
        raw_text = f'{{"legacy_value": {constant}}}'
    original_bytes = raw_text.encode("utf-8")
    path.write_bytes(original_bytes)

    result = compat.load_vnext_settings(path)

    if source_shape == "flat":
        assert result.status == compat.SettingsPersistenceStatus.SUCCESS
        assert result.settings is not None
        assert result.settings.intent.local_llm.extra_body == {
            "reasoning_effort": "none",
            "temperature": 0.6,
        }
        assert path.read_bytes() == original_bytes
        assert not list(tmp_path.glob("*.bak"))
        return

    assert result.status == compat.SettingsPersistenceStatus.PARSE_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))


def test_save_rejects_non_finite_canonical_value_without_overwrite(tmp_path: Path) -> None:
    compat = _compat()
    path = tmp_path / "settings.json"
    original_bytes = b"existing-settings"
    path.write_bytes(original_bytes)
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            overlay=replace(
                settings.intent.overlay,
                desktop_flet=replace(
                    settings.intent.overlay.desktop_flet,
                    visual=replace(
                        settings.intent.overlay.desktop_flet.visual,
                        background_alpha=float("nan"),
                    ),
                ),
            ),
        ),
    )

    result = compat.save_vnext_settings(path, settings)

    assert result.status == compat.SettingsPersistenceStatus.SAVE_FAILED
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob(".settings.json.*.tmp"))


@pytest.mark.parametrize(
    ("raw", "case_id"),
    [
        (
            {"settings_version": VNEXT_SETTINGS_SCHEMA_VERSION, "intent": {}},
            "missing-state",
        ),
        (
            {"settings_version": VNEXT_SETTINGS_SCHEMA_VERSION, "intent": [], "state": {}},
            "non-object-intent",
        ),
        (
            {"settings_version": VNEXT_SETTINGS_SCHEMA_VERSION, "intent": {}, "state": []},
            "non-object-state",
        ),
    ],
)
def test_malformed_current_vnext_top_level_shape_fails_without_backup_or_overwrite(
    tmp_path: Path,
    raw: dict[str, Any],
    case_id: str,
) -> None:
    compat = _compat()
    path = tmp_path / f"{case_id}.json"
    original_bytes = _write_json_bytes(path, raw)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))


def test_unsupported_future_canonical_version_fails_without_backup_or_overwrite(
    tmp_path: Path,
) -> None:
    compat = _compat()
    serialization = _serialization()
    path = tmp_path / "future-settings.json"
    raw = serialization.to_dict(AppSettingsVNext())
    raw["settings_version"] = VNEXT_SETTINGS_SCHEMA_VERSION + 1
    original_bytes = _write_json_bytes(path, raw)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert result.settings is None
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))


def test_missing_cloud_free_tier_defaults_to_gemini() -> None:
    migration = _migration()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["settings_version"] = 37
    raw["intent"]["stt"].pop("cloud_free_tier_providers", None)
    raw["state"]["provider_verification"]["deepgram"] = {
        "status": "verified",
        "provider": "deepgram",
        "secret_key": "deepgram_api_key",
        "secret_revision": "secret-r1",
        "secret_fingerprint": None,
        "verifier_context": {"flow": "settings.verify_api_key"},
        "verifier_evidence": {"verifier": "deepgram"},
    }

    loaded = migration.from_dict(raw)

    assert loaded.intent.stt.cloud_free_tier_providers == ["gemini_transcribe"]
    assert loaded.intent.stt.provider != "rolling_free"


def test_explicit_cloud_free_tier_selection_is_preserved() -> None:
    migration = _migration()
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["intent"]["stt"]["cloud_free_tier_providers"] = ["elevenlabs_scribe"]

    loaded = migration.from_dict(raw)
    assert loaded.intent.stt.cloud_free_tier_providers == ["elevenlabs_scribe"]


def test_managed_operation_identity_round_trips_through_canonical_text() -> None:
    from puripuly_heart.config.settings_vnext.schema import ManagedConnectionState

    serialization = _serialization()
    settings = AppSettingsVNext()
    managed = ManagedConnectionState(
        installation_id="install-1",
        pending_managed_operation_id="ph-mop-v1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pending_managed_operation_source="discord",
        pending_managed_operation_installation_id="install-1",
        pending_managed_operation_state="CREATING",
    )
    settings = replace(settings, state=replace(settings.state, managed_connection=managed))
    restored = serialization.from_dict(json.loads(serialization.to_json_text(settings)))
    persisted = restored.state.managed_connection
    assert persisted.pending_managed_operation_id == ("ph-mop-v1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert persisted.pending_managed_operation_source == "discord"
    assert persisted.pending_managed_operation_installation_id == "install-1"
    assert persisted.pending_managed_operation_state == "CREATING"


def test_managed_operation_identity_normalizes_all_or_nothing() -> None:
    from puripuly_heart.config.settings_vnext.schema import ManagedConnectionState

    partial = ManagedConnectionState(
        installation_id="install-1",
        pending_managed_operation_id="ph-mop-v1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        pending_managed_operation_source="qq",
        pending_managed_operation_installation_id="install-1",
        pending_managed_operation_state="BOGUS",
    )
    assert partial.pending_managed_operation_id is None
    assert partial.pending_managed_operation_source is None
    assert partial.pending_managed_operation_installation_id is None
    assert partial.pending_managed_operation_state is None
    fresh = ManagedConnectionState(installation_id="install-1")
    assert fresh.pending_managed_operation_id is None


@pytest.mark.parametrize(
    ("path", "invalid"),
    [
        (("intent", "osc", "send_port"), "9000"),
        (("intent", "audio"), []),
        (("intent", "translation", "connection_history"), {"model": 3}),
        (("intent", "stt", "custom_terms"), {"en": [1]}),
    ],
)
def test_repeated_schema_loading_revalidates_wrong_and_nested_types(
    path: tuple[str, ...], invalid: object
) -> None:
    serialization = _serialization()
    for locale in ("en", "ko"):
        valid = serialization.to_dict(AppSettingsVNext())
        valid["intent"]["ui"]["locale"] = locale
        assert serialization.from_dict(valid).intent.ui.locale == locale
        malformed = serialization.to_dict(serialization.from_dict(valid))
        target = malformed
        for member in path[:-1]:
            target = target[member]
        target[path[-1]] = invalid
        with pytest.raises(ValueError, match=r"settings\."):
            serialization.from_dict(malformed)
        assert serialization.from_dict(valid).intent.ui.locale == locale


def test_repeated_schema_loading_preserves_independent_mutable_settings() -> None:
    serialization = _serialization()
    raw = serialization.to_dict(AppSettingsVNext())
    raw["intent"]["local_llm"]["extra_body"] = {"options": {"labels": ["alpha"]}}
    first = serialization.from_dict(raw)
    second = serialization.from_dict(raw)

    first.intent.local_llm.extra_body["options"]["labels"].append("beta")
    first.intent.translation.connection_history["synthetic-model"] = "byok"

    assert second.intent.local_llm.extra_body == {"options": {"labels": ["alpha"]}}
    assert raw["intent"]["local_llm"]["extra_body"] == {"options": {"labels": ["alpha"]}}
    assert "synthetic-model" not in second.intent.translation.connection_history
    assert "synthetic-model" not in raw["intent"]["translation"]["connection_history"]


def _structured_records(caplog):
    from puripuly_heart.core.diagnostics import DIAGNOSTIC_RECORD_ATTRIBUTE

    return [
        getattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE)
        for record in caplog.records
        if hasattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE)
    ]


def test_pre_vnext_input_resets_to_defaults_with_single_safe_diagnostic(
    tmp_path: Path, caplog
) -> None:
    import logging

    compat = _compat()
    secret_marker = "SECRET_MARKER_9f2c"
    raw_text = (
        '{"some_legacy_key": "legacy-value", "api_key": "'
        + secret_marker
        + '", "nested": {"url": "http://legacy.invalid/v1"}}'
    )
    path = tmp_path / "distinctive-pre-vnext-name.json"
    original_bytes = raw_text.encode("utf-8")
    path.write_bytes(original_bytes)
    caplog.set_level(logging.WARNING)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.SUCCESS
    assert result.settings is not None
    assert result.settings == compat.defaults.new_settings_for_reset()
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak"))

    records = _structured_records(caplog)
    assert len(records) == 1
    diagnostics, severity, disposition, stage, _exception = records[0]
    assert severity == "warning"
    assert disposition == "degraded"
    assert stage == "load"
    assert diagnostics.component == "settings_repository"
    assert diagnostics.operation == "load"
    assert diagnostics.category == "transaction"
    assert diagnostics.visibility == "basic"
    assert diagnostics.content_policy == "metadata_only"
    assert diagnostics.status_code is None
    dumped = repr(diagnostics)
    assert secret_marker not in dumped
    assert "legacy-value" not in dumped
    assert "legacy.invalid" not in dumped
    assert path.name not in dumped
    assert str(path) not in dumped


def test_unsupported_canonical_version_fails_explicitly_without_reset_or_backup(
    tmp_path: Path, caplog
) -> None:
    import logging

    compat = _compat()
    path = tmp_path / "future-version-settings.json"
    raw_bytes = _write_json_bytes(
        path,
        {
            "settings_version": VNEXT_SETTINGS_SCHEMA_VERSION + 1,
            "intent": {},
            "state": {},
        },
    )
    caplog.set_level(logging.WARNING)

    result = compat.load_vnext_settings(path)

    assert result.status == compat.SettingsPersistenceStatus.MIGRATION_FAILED
    assert result.settings is None
    assert result.error is not None
    assert path.read_bytes() == raw_bytes
    assert not list(tmp_path.glob("*.bak"))
    assert _structured_records(caplog) == []
