from __future__ import annotations

import asyncio

import pytest

from research_kb_app.runtime import AppOperationError, AppRuntime, OperationCoordinator


def test_coordinator_serializes_intake_and_catalog_rebuild() -> None:
    coordinator = OperationCoordinator()
    lease = coordinator.acquire("intake")

    assert coordinator.public() == {
        "category": "intake",
        "state": "running",
        "job_id": None,
        "diagnostic_code": None,
    }
    with pytest.raises(AppOperationError) as busy:
        coordinator.acquire("catalog_rebuild")
    assert busy.value.code == "RKBAPP-OPERATION-BUSY"

    coordinator.set_job_id(lease, "JOB-00000001")
    coordinator.complete(lease)
    assert coordinator.public()["state"] == "current"


def test_coordinator_accepts_dedicated_tag_category_and_serializes_it() -> None:
    coordinator = OperationCoordinator()
    lease = coordinator.acquire("tag")

    assert coordinator.public()["category"] == "tag"
    with pytest.raises(AppOperationError) as busy:
        coordinator.acquire("agent_task")
    assert busy.value.code == "RKBAPP-OPERATION-BUSY"

    coordinator.complete(lease)


def test_stale_lease_cannot_release_a_new_operation() -> None:
    coordinator = OperationCoordinator()
    old = coordinator.acquire("intake")
    coordinator.complete(old)
    current = coordinator.acquire("catalog_rebuild")

    with pytest.raises(AppOperationError) as stale:
        coordinator.fail(old, "RKBAPP-STALE")

    assert stale.value.code == "RKBAPP-OPERATION-STALE"
    assert coordinator.is_owner(current)
    assert coordinator.public()["state"] == "building"


def test_matching_active_intake_cancel_is_signal_only_and_cas_bound() -> None:
    coordinator = OperationCoordinator()
    lease = coordinator.acquire("intake")
    coordinator.bind_cancellation(
        lease,
        job_id="job_1234",
        job_state_id="jobstate_1234",
        job_state_digest="a" * 64,
    )
    expected = {"state_id": "jobstate_1234", "state_digest": "a" * 64}

    assert coordinator.request_cancel("job_other", expected) is None
    assert not coordinator.cancel_requested(lease)
    with pytest.raises(AppOperationError) as stale:
        coordinator.request_cancel(
            "job_1234",
            {"state_id": "jobstate_other", "state_digest": "b" * 64},
        )
    assert stale.value.code == "RKBAPP-TRUSTED-PARSE-CANCEL-STALE"
    assert not coordinator.cancel_requested(lease)

    assert coordinator.request_cancel("job_1234", expected) == "accepted"
    assert coordinator.cancel_requested(lease)
    assert coordinator.request_cancel("job_1234", expected) == "accepted"


def test_promotion_barrier_makes_late_cancel_explicit() -> None:
    coordinator = OperationCoordinator()
    lease = coordinator.acquire("intake")
    coordinator.bind_cancellation(
        lease,
        job_id="job_1234",
        job_state_id="jobstate_1234",
        job_state_digest="a" * 64,
    )
    expected = {"state_id": "jobstate_1234", "state_digest": "a" * 64}

    assert coordinator.begin_promotion(lease) is True
    assert coordinator.request_cancel("job_1234", expected) == "too_late"
    assert not coordinator.cancel_requested(lease)
    coordinator.complete(lease)


def test_runtime_close_is_a_writer_barrier() -> None:
    async def scenario() -> None:
        runtime = object.__new__(AppRuntime)
        release = asyncio.Event()
        worker = asyncio.create_task(release.wait())
        runtime._tasks = set()
        runtime._track(worker)

        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not closing.done()

        release.set()
        await closing

    asyncio.run(scenario())


def test_runtime_close_waits_for_successor_tasks() -> None:
    async def scenario() -> None:
        runtime = object.__new__(AppRuntime)
        runtime._tasks = set()
        release_successor = asyncio.Event()

        async def parent() -> None:
            await asyncio.sleep(0)
            runtime._track(asyncio.create_task(release_successor.wait()))

        runtime._track(asyncio.create_task(parent()))
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not closing.done()

        release_successor.set()
        await closing

    asyncio.run(scenario())


def test_runtime_close_signals_active_trusted_parse_and_waits_for_settlement() -> None:
    async def scenario() -> None:
        runtime = object.__new__(AppRuntime)
        runtime.operation = OperationCoordinator()
        operation_lease = runtime.operation.acquire("intake")
        runtime.operation.bind_cancellation(
            operation_lease,
            job_id="job_1234",
            job_state_id="jobstate_1234",
            job_state_digest="a" * 64,
        )
        runtime._tasks = set()

        async def worker() -> None:
            while not runtime.operation.cancel_requested(operation_lease):
                await asyncio.sleep(0)
            runtime.operation.fail(operation_lease, "RKBC-OPERATION-CANCELLED")

        runtime._track(asyncio.create_task(worker()))
        await runtime.close()

        assert runtime.operation.is_busy() is False
        assert runtime.operation.public()["diagnostic_code"] == "RKBC-OPERATION-CANCELLED"

    asyncio.run(scenario())
