from __future__ import annotations

from types import SimpleNamespace


def test_entrypoint_refuses_incompatible_core_before_loading_launcher(monkeypatch, capsys) -> None:
    from research_kb_app import entrypoint

    calls: list[str] = []

    def refuse_runtime(_argv):
        calls.append("compatibility")
        raise entrypoint.RuntimeCompatibilityError(
            "Installed Research KB Core is incompatible with this App build"
        )

    monkeypatch.setattr(entrypoint, "_verify_runtime", refuse_runtime)
    monkeypatch.setattr(
        entrypoint,
        "_load_launcher_start",
        lambda: calls.append("launcher") or (lambda *_args, **_kwargs: 0),
    )

    assert entrypoint.main(["--no-browser"]) == 2
    assert calls == ["compatibility"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Research KB App refused to start: "
        "Installed Research KB Core is incompatible with this App build\n"
    )


def test_entrypoint_reports_installation_gap_for_launcher_import_failure(monkeypatch, capsys) -> None:
    from research_kb_app import entrypoint

    calls: list[str] = []
    monkeypatch.setattr(entrypoint, "_verify_runtime", lambda argv: object())

    def fail_import():
        calls.append("launcher")
        raise ModuleNotFoundError("No module named 'missing_feature_dependency'")

    monkeypatch.setattr(entrypoint, "_load_launcher_start", fail_import)

    assert entrypoint.main(["--no-browser"]) == 2
    assert calls == ["launcher"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Research KB App refused to start: App installation is incomplete\n"
    )
    assert "missing_feature_dependency" not in captured.err  # no internal import detail leak


def test_entrypoint_passes_verified_identity_to_launcher(monkeypatch) -> None:
    from research_kb_app import entrypoint

    compatibility = object()
    calls: list[object] = []
    monkeypatch.setattr(entrypoint, "_verify_runtime", lambda argv: compatibility)

    def launcher_start(argv, verified_compatibility):
        calls.extend([argv, verified_compatibility])
        return 17

    monkeypatch.setattr(entrypoint, "_load_launcher_start", lambda: launcher_start)

    assert entrypoint.main(["--profile", "default"]) == 17
    assert calls == [["--profile", "default"], compatibility]


def test_launcher_skips_duplicate_identity_check_for_verified_entrypoint(monkeypatch, tmp_path) -> None:
    from research_kb_app import launcher

    compatibility = object()
    config = SimpleNamespace(
        path=tmp_path / "config.json",
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
    )
    calls: list[str] = []
    monkeypatch.setattr(launcher, "load_compatibility", lambda _path: calls.append("load"))
    monkeypatch.setattr(launcher, "verify_installed_core", lambda _value: calls.append("verify"))
    monkeypatch.setattr(launcher, "load_app_config", lambda _path: config)
    monkeypatch.setattr(launcher, "preflight_storage", lambda _config: calls.append("storage"))

    def stop_after_storage(_path) -> None:
        raise OSError("stop after verified identity")

    monkeypatch.setattr(launcher, "_ensure_directory", stop_after_storage)

    assert (
        launcher._start_after_compatibility(
            ["--config", str(config.path), "--no-browser"],
            compatibility,
        )
        == 2
    )
    assert calls == ["storage"]
