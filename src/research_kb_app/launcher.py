from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import uvicorn

from research_kb_app.compatibility import CompatibilityError, load_compatibility, verify_installed_core
from research_kb_app.config import AppConfig, AppConfigError, load_app_config
from research_kb_app.egress import EgressPolicyService
from research_kb_app.errors import AppOperationError
from research_kb_app.lifecycle_receipts import AppendOnlyReceiptStore
from research_kb_app.product_profile import ManagedProductProfileStore, ensure_managed_profile_root
from research_kb_app.profile_instance import ProfileInstanceGuard
from research_kb_app.security import new_startup_token
from research_kb_app.storage import StoragePreflightError, preflight_storage
from research_kb_app.windows_security import WindowsNamedMutexFactory, WindowsRootSecurityService

if TYPE_CHECKING:
    from research_kb_app.setup_runtime import SetupRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the Research KB localhost App")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path, help="absolute App configuration path")
    source.add_argument("--profile", help="managed product profile ID")
    parser.add_argument("--compatibility", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help="do not open the default browser")
    return parser


def create_app(*args: Any, **kwargs: Any) -> Any:
    # Core compatibility must be checked before importing feature-specific services.
    from research_kb_app.api import create_app as create_fastapi_app

    return create_fastapi_app(*args, **kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        compatibility = load_compatibility(args.compatibility)
        verify_installed_core(compatibility)
    except (CompatibilityError, OSError, ValueError) as error:
        print(f"Research KB App refused to start: {error}", file=sys.stderr)
        return 2
    return _start(args, compatibility)


def _start_after_compatibility(argv: Sequence[str], compatibility: Any) -> int:
    return _start(build_parser().parse_args(argv), compatibility)


def _start(args: argparse.Namespace, compatibility: Any) -> int:
    try:
        config, setup_runtime, instance_key = _launch_context(args)
        preflight_storage(config)
        _ensure_directory(config.state_root)
        _ensure_directory(config.log_root)
    except (AppConfigError, AppOperationError, StoragePreflightError, OSError, ValueError) as error:
        print(f"Research KB App refused to start: {error}", file=sys.stderr)
        return 2

    receipt_root = config.state_root / "receipts"
    instance_receipts = AppendOnlyReceiptStore(receipt_root / "profile-instance", "profile-instance")
    egress = EgressPolicyService(
        receipt_writer=AppendOnlyReceiptStore(receipt_root / "egress", "egress").append,
    )
    try:
        with ProfileInstanceGuard(
            config.state_root / "runtime",
            instance_key,
            mutex_factory=WindowsNamedMutexFactory(timeout_seconds=5),
            receipt_store=instance_receipts,
        ):
            return _serve(config, compatibility, setup_runtime, egress, args)
    except AppOperationError as error:
        print(f"Research KB App refused to start: {error}", file=sys.stderr)
        return 2


def _serve(
    config: AppConfig,
    compatibility: Any,
    setup_runtime: SetupRuntime | None,
    egress_policy: EgressPolicyService,
    args: argparse.Namespace,
) -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2048)
    host, port = listener.getsockname()
    expected_host = f"{host}:{port}"
    origin = f"http://{expected_host}"
    token = new_startup_token()
    log_path = config.log_root / _log_filename()
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    app = create_app(
        config,
        compatibility,
        startup_token=token,
        expected_host=expected_host,
        expected_origin=origin,
        setup_runtime=setup_runtime,
        egress_policy=egress_policy,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            access_log=False,
            log_config=None,
            server_header=False,
        )
    )

    print(f"URL: {origin}/", flush=True)
    print(f"ONE-TIME TOKEN: {token}", flush=True)
    print(f"LOG: {log_path}", flush=True)

    shutdown_thread = threading.Thread(
        target=_wait_for_shutdown,
        args=(app.state.runtime.shutdown_requested, server),
        daemon=True,
    )
    shutdown_thread.start()
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(server, origin), daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
    return 0


def _ensure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def _launch_context(args: argparse.Namespace) -> tuple[AppConfig, SetupRuntime | None, str]:
    from research_kb_app.setup_runtime import DEFAULT_REQUEST_BUDGETS, SetupRuntime

    if args.config is not None:
        config = load_app_config(args.config)
        return config, None, f"explicit-config:{config.path}"

    profile_id = args.profile or "default"
    root_security = WindowsRootSecurityService()
    profile_root = ensure_managed_profile_root(profile_id, root_security)
    profile_store = ManagedProductProfileStore(profile_root, profile_id)
    profile_store.ensure_layout()
    frontend_root = _frontend_root()
    current = profile_store.load_current(missing_ok=True)
    if current is None:
        config = AppConfig(
            path=profile_store.current_pointer_path,
            workspaces=(),
            state_root=profile_store.state_root,
            log_root=profile_store.log_root,
            frontend_root=frontend_root,
            request_budgets=DEFAULT_REQUEST_BUDGETS,
            obsidian_targets=(),
        )
    else:
        config = profile_store.to_app_config(frontend_root=frontend_root)
    setup = SetupRuntime(
        profile_store=profile_store,
        frontend_root=frontend_root,
        root_security=root_security,
    )
    return config, setup, f"managed-profile:{profile_id}"


def _frontend_root() -> Path:
    packaged = Path(__file__).resolve().parent / "web_dist"
    if (packaged / "index.html").is_file():
        return packaged
    source = Path(__file__).resolve().parents[2] / "web" / "release"
    if (source / "index.html").is_file():
        return source
    raise AppConfigError("Packaged frontend is unavailable")


def _log_filename() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"research-kb-app-{stamp}.log"


def _wait_for_shutdown(event: threading.Event, server: uvicorn.Server) -> None:
    event.wait()
    server.should_exit = True


def _open_when_ready(server: uvicorn.Server, origin: str) -> None:
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if server.started:
        webbrowser.open(origin)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
