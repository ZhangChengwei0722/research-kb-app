from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from research_kb_app.errors import AppOperationError
from research_kb_app.exchange_custody import (
    cleanup_abandoned_exchange_files,
    create_exchange_output,
    stream_exchange_upload,
)


async def _chunks(payload: bytes, width: int = 5):
    for index in range(0, len(payload), width):
        yield payload[index : index + width]


def test_exchange_upload_streams_into_owned_path_and_revalidates_identity(tmp_path: Path) -> None:
    archive = b"PK\x03\x04" + b"synthetic-exchange"
    upload = asyncio.run(
        stream_exchange_upload(
            _chunks(archive),
            state_root=tmp_path,
            max_archive_bytes=len(archive),
            declared_length=len(archive),
        )
    )

    assert upload.size_bytes == len(archive)
    assert upload.path.is_relative_to(tmp_path / "exchange-spool")
    assert upload.path.name == "bundle.rkb-exchange.zip"
    assert upload.open().read() == archive
    upload.path.write_bytes(b"PK\x03\x04" + b"tampered-exchange")
    with pytest.raises(AppOperationError) as caught:
        upload.open()
    assert caught.value.code == "RKBAPP-EXCHANGE-CONTENT"
    assert upload.cleanup() is True
    assert not upload.operation_root.exists()


def test_exchange_upload_rejects_bad_length_prefix_and_budget_without_residue(tmp_path: Path) -> None:
    cases = [
        (b"PK\x03\x04payload", 3, 3),
        (b"not-a-zip", 9, 9),
        (b"PK\x03\x04payload", 12, 11),
    ]
    for payload, declared, limit in cases:
        with pytest.raises(AppOperationError):
            asyncio.run(
                stream_exchange_upload(
                    _chunks(payload),
                    state_root=tmp_path,
                    max_archive_bytes=limit,
                    declared_length=declared,
                )
            )
    managed = tmp_path / "exchange-spool"
    assert not managed.exists() or not list(managed.iterdir())


def test_exchange_output_is_create_only_and_cleanup_is_marker_bound(tmp_path: Path) -> None:
    output = create_exchange_output(tmp_path)
    output.path.write_bytes(b"PK\x03\x04archive")
    artifact = output.finalize(max_archive_bytes=1024)
    foreign = tmp_path / "exchange-spool" / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("user-owned", encoding="utf-8")

    assert artifact.open().read() == b"PK\x03\x04archive"
    assert cleanup_abandoned_exchange_files(tmp_path) == 1
    assert not artifact.operation_root.exists()
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "user-owned"
