from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import synthetic_pdf_bytes
from research_kb_app.multipart import cleanup_abandoned_uploads, parse_multipart_stream
from research_kb_app.runtime import AppOperationError


def _body(parts: list[tuple[str, str | None, str, bytes]], boundary: str = "rkbboundary") -> bytes:
    chunks: list[bytes] = []
    for name, filename, content_type, content in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f"Content-Disposition: {disposition}\r\n".encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


async def _chunks(payload: bytes, width: int = 7):
    for index in range(0, len(payload), width):
        yield payload[index : index + width]


def _metadata() -> bytes:
    return json.dumps(
        {
            "idempotency_key": "upload-1",
            "requested_operation": "basic_paper_card",
            "document_route": "primary",
            "route_reason": None,
            "bibliography": {"title": "Synthetic upload", "authors": [], "year": 2026, "doi": None},
        }
    ).encode("utf-8")


def test_chunk_split_multipart_uses_owned_spool_and_ignores_filename(tmp_path: Path) -> None:
    pdf = synthetic_pdf_bytes()
    payload = _body(
        [
            ("file", "../../private.pdf", "application/pdf", pdf),
            ("metadata", None, "application/json", _metadata()),
        ]
    )
    parsed = asyncio.run(
        parse_multipart_stream(
            _chunks(payload),
            content_type="multipart/form-data; boundary=rkbboundary",
            state_root=tmp_path,
            max_pdf_bytes=len(pdf) + 1,
        )
    )

    assert parsed.metadata["idempotency_key"] == "upload-1"
    assert parsed.upload.size_bytes == len(pdf)
    assert parsed.upload.path.name == "source.pdf.partial"
    assert parsed.upload.path.is_relative_to(tmp_path / "upload-spool")
    assert "private.pdf" not in str(parsed.upload.path)
    parsed.upload.cleanup()
    assert not parsed.upload.operation_root.exists()


def test_single_large_transport_chunk_is_fed_to_parser_incrementally(tmp_path: Path) -> None:
    pdf = b"%PDF-" + b"x" * (128 * 1024)
    payload = _body(
        [
            ("metadata", None, "application/json", _metadata()),
            ("file", "source.pdf", "application/pdf", pdf),
        ]
    )

    parsed = asyncio.run(
        parse_multipart_stream(
            _chunks(payload, width=len(payload)),
            content_type="multipart/form-data; boundary=rkbboundary",
            state_root=tmp_path,
            max_pdf_bytes=len(pdf),
        )
    )

    assert parsed.upload.size_bytes == len(pdf)
    parsed.upload.cleanup()


@pytest.mark.parametrize(
    "parts",
    [
        [("metadata", None, "application/json", _metadata())],
        [
            ("file", "one.pdf", "application/pdf", synthetic_pdf_bytes()),
            ("file", "two.pdf", "application/pdf", synthetic_pdf_bytes()),
            ("metadata", None, "application/json", _metadata()),
        ],
        [
            ("file", "one.pdf", "application/pdf", synthetic_pdf_bytes()),
            ("metadata", None, "application/json", _metadata()),
            ("extra", None, "text/plain", b"unexpected"),
        ],
    ],
)
def test_multipart_requires_exactly_one_file_and_metadata(tmp_path: Path, parts) -> None:
    with pytest.raises(AppOperationError) as rejected:
        asyncio.run(
            parse_multipart_stream(
                _chunks(_body(parts)),
                content_type="multipart/form-data; boundary=rkbboundary",
                state_root=tmp_path,
                max_pdf_bytes=1024 * 1024,
            )
        )
    assert rejected.value.status_code == 400
    managed = tmp_path / "upload-spool"
    assert not managed.exists() or not list(managed.iterdir())


def test_oversized_or_non_pdf_upload_is_rejected_and_cleaned(tmp_path: Path) -> None:
    for file_bytes, limit in [(synthetic_pdf_bytes(), 20), (b"not-a-pdf", 1024)]:
        payload = _body(
            [
                ("file", "source.pdf", "application/pdf", file_bytes),
                ("metadata", None, "application/json", _metadata()),
            ]
        )
        with pytest.raises(AppOperationError):
            asyncio.run(
                parse_multipart_stream(
                    _chunks(payload),
                    content_type="multipart/form-data; boundary=rkbboundary",
                    state_root=tmp_path,
                    max_pdf_bytes=limit,
                )
            )
    managed = tmp_path / "upload-spool"
    assert not managed.exists() or not list(managed.iterdir())


def test_duplicate_metadata_json_key_is_rejected_and_cleaned(tmp_path: Path) -> None:
    duplicate_metadata = (
        b'{"idempotency_key":"first","idempotency_key":"second",'
        b'"requested_operation":"basic_paper_card","document_route":"primary",'
        b'"route_reason":null,"bibliography":{}}'
    )
    payload = _body(
        [
            ("file", "source.pdf", "application/pdf", synthetic_pdf_bytes()),
            ("metadata", None, "application/json", duplicate_metadata),
        ]
    )

    with pytest.raises(AppOperationError) as rejected:
        asyncio.run(
            parse_multipart_stream(
                _chunks(payload),
                content_type="multipart/form-data; boundary=rkbboundary",
                state_root=tmp_path,
                max_pdf_bytes=1024 * 1024,
            )
        )

    assert rejected.value.code == "RKBAPP-MULTIPART"
    managed = tmp_path / "upload-spool"
    assert not managed.exists() or not list(managed.iterdir())


def test_startup_cleanup_removes_only_marked_abandoned_spools(tmp_path: Path) -> None:
    pdf = synthetic_pdf_bytes()
    parsed = asyncio.run(
        parse_multipart_stream(
            _chunks(
                _body(
                    [
                        ("file", "source.pdf", "application/pdf", pdf),
                        ("metadata", None, "application/json", _metadata()),
                    ]
                )
            ),
            content_type="multipart/form-data; boundary=rkbboundary",
            state_root=tmp_path,
            max_pdf_bytes=len(pdf) + 1,
        )
    )
    foreign = tmp_path / "upload-spool" / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("user-owned", encoding="utf-8")

    assert cleanup_abandoned_uploads(tmp_path) == 1
    assert not parsed.upload.operation_root.exists()
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "user-owned"
