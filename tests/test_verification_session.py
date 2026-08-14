"""入群验证会话管理与超时调度测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime

import pytest

from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.session import (
    SessionRecord,
    SessionStore,
    get_session_store,
)


@pytest.fixture(autouse=True)
def _fresh_store() -> Generator[None]:
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        session,
    )

    before = session._store
    session._store = None
    try:
        yield
    finally:
        session._store = before


def _start(store: SessionStore, *, user: str = "10001") -> SessionRecord:
    return store.start(
        group_id="123",
        user_id=user,
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )


@pytest.mark.asyncio
async def test_start_creates_waiting_session() -> None:
    store = SessionStore()
    record = _start(store)

    assert record.status == "waiting"
    assert record.retry_count == 0
    assert record.is_muted is False
    assert record.trigger_time.tzinfo is UTC
    assert record.expires_at > datetime.now(UTC)
    assert store.is_waiting("123", "10001") is True
    store.close()


@pytest.mark.asyncio
async def test_mark_retry_increments() -> None:
    store = SessionStore()
    _start(store)

    updated = store.mark_retry("123", "10001")
    assert updated is not None
    assert updated.retry_count == 1
    after = store.get("123", "10001")
    assert after is not None
    assert after.retry_count == 1
    store.close()


@pytest.mark.asyncio
async def test_end_cancels_timeout() -> None:
    store = SessionStore()
    _start(store)
    assert len(store._timeout_tasks) == 1

    store.end("123", "10001", status="approved")
    ended = store.get("123", "10001")
    assert ended is not None
    assert ended.status == "approved"
    assert len(store._timeout_tasks) == 0
    store.close()


@pytest.mark.asyncio
async def test_remove_clears_session() -> None:
    store = SessionStore()
    _start(store)

    removed = store.remove("123", "10001")
    assert removed is not None
    assert store.get("123", "10001") is None
    store.close()


@pytest.mark.asyncio
async def test_timeout_callback_fires() -> None:
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_timeout_callback(callback)
    _start(store)
    await store._run_timeout(("123", "10001"), 0.0)

    assert fired == [("123", "10001")]
    store.close()


@pytest.mark.asyncio
async def test_timeout_does_not_fire_after_end() -> None:
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_timeout_callback(callback)
    record = _start(store)
    store.end(record.group_id, record.user_id, status="approved")

    await store._run_timeout(("123", "10001"), 0.0)
    assert fired == []
    store.close()


@pytest.mark.asyncio
async def test_start_overwrites_previous_session() -> None:
    store = SessionStore()
    _start(store, user="10001")
    _start(store, user="10001")

    assert len(store._sessions) == 1
    assert len(store._timeout_tasks) == 1
    store.close()


def test_get_session_store_singleton() -> None:
    assert get_session_store() is get_session_store()


@pytest.mark.asyncio
async def test_scheduled_timeout_cancelled_by_end() -> None:
    """已调度超时任务在会话结束后不应触发。"""
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_timeout_callback(callback)
    _start(store)
    store.end("123", "10001", status="approved")

    # 给取消时间片
    await asyncio.sleep(0)
    await asyncio.sleep(0.01)
    assert fired == []
    store.close()


@pytest.mark.asyncio
async def test_set_muted_updates_state() -> None:
    store = SessionStore()
    _start(store)

    updated = store.set_muted("123", "10001", is_muted=True)
    assert updated is not None
    assert updated.is_muted is True
    after_set = store.get("123", "10001")
    assert after_set is not None
    assert after_set.is_muted is True

    store.set_muted("123", "10001", is_muted=False)
    after_unset = store.get("123", "10001")
    assert after_unset is not None
    assert after_unset.is_muted is False
    store.close()


@pytest.mark.asyncio
async def test_set_muted_missing_session_returns_none() -> None:
    store = SessionStore()
    assert store.set_muted("123", "99999", is_muted=True) is None


@pytest.mark.asyncio
async def test_remove_group_clears_all_sessions() -> None:
    store = SessionStore()
    _start(store, user="10001")
    _start(store, user="10002")
    _start(store, user="10003")
    _start(store, user="10004")

    removed = store.remove_group("123")
    assert len(removed) == 4
    assert store.get("123", "10001") is None
    assert store.get("123", "10002") is None
    assert store.get("123", "10003") is None
    assert store.get("123", "10004") is None
    assert store.list_waiting() == ()
    assert len(store._timeout_tasks) == 0
    store.close()
