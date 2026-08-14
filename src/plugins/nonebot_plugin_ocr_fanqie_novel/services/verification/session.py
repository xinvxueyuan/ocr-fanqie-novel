"""入群验证会话管理（FR1/FR7/FR8 状态与超时）。

PRD 第 8 节允许单机内存会话存储，重启丢失无关紧要。本模块在内存中
维护活跃验证会话（``(group_id, user_id)`` 为键），并为每个会话调度
一个超时协程（FR7）。会话状态变化时同步更新数据库以便审计回溯。

"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nonebot import logger

from ...core.config import plugin_config

_SessionKey = tuple[str, str]

TimeoutCallback = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """内存中的活跃验证会话记录。"""

    group_id: str
    user_id: str
    bot_id: str
    platform_id: str
    adapter_id: str
    protocol_id: str | None
    trigger_time: datetime
    expires_at: datetime
    retry_count: int = 0
    is_muted: bool = False
    last_extracted: dict | None = None
    status: str = "waiting"

    def to_db_dict(self) -> dict:
        """转换为仓库层 upsert 所需的字段。"""
        return {
            "platform_id": self.platform_id,
            "adapter_id": self.adapter_id,
            "protocol_id": self.protocol_id,
            "bot_id": self.bot_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "status": self.status,
            "retry_count": self.retry_count,
            "is_muted": self.is_muted,
            "last_extracted": self.last_extracted,
            "trigger_time": self.trigger_time,
            "expires_at": self.expires_at,
        }


class SessionStore:
    """活跃验证会话的内存存储与超时调度。"""

    def __init__(self) -> None:
        self._sessions: dict[_SessionKey, SessionRecord] = {}
        self._timeout_tasks: dict[_SessionKey, asyncio.Task] = {}
        self._timeout_callback: TimeoutCallback | None = None
        self._closed = False

    def set_timeout_callback(self, callback: TimeoutCallback) -> None:
        """注册超时回调（由编排层注入，避免循环依赖）。"""
        self._timeout_callback = callback

    def get(self, group_id: str, user_id: str) -> SessionRecord | None:
        """返回活跃会话记录；不存在时返回 ``None``。"""
        return self._sessions.get((group_id, user_id))

    def is_waiting(self, group_id: str, user_id: str) -> bool:
        """该成员是否存在处于 waiting 状态的会话。"""
        record = self.get(group_id, user_id)
        return record is not None and record.status == "waiting"

    def list_waiting(self) -> tuple[SessionRecord, ...]:
        """返回所有 waiting 状态会话的快照。"""
        return tuple(
            record
            for record in self._sessions.values()
            if record.status == "waiting"
        )

    def start(
        self,
        *,
        group_id: str,
        user_id: str,
        bot_id: str,
        platform_id: str,
        adapter_id: str,
        protocol_id: str | None,
    ) -> SessionRecord:
        """开启（或重置）一个验证会话并调度超时。

        PRD 10：同一用户在同一群仅保留最新会话，旧会话超时任务取消。

        """
        key = (group_id, user_id)
        self._cancel_timeout(key)

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=plugin_config.fanqie_response_timeout)
        record = SessionRecord(
            group_id=group_id,
            user_id=user_id,
            bot_id=bot_id,
            platform_id=platform_id,
            adapter_id=adapter_id,
            protocol_id=protocol_id,
            trigger_time=now,
            expires_at=expires_at,
        )
        self._sessions[key] = record
        self._schedule_timeout(key)
        return record

    def mark_retry(self, group_id: str, user_id: str) -> SessionRecord | None:
        """识别失败一次，重试计数 +1。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["retry_count"] = record.retry_count + 1
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def update_last_extracted(
        self,
        group_id: str,
        user_id: str,
        last_extracted: dict | None,
        *,
        is_muted: bool | None = None,
        status: str | None = None,
    ) -> SessionRecord | None:
        """更新会话的提取结果、禁言状态或状态。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["last_extracted"] = last_extracted
        if is_muted is not None:
            data["is_muted"] = is_muted
        if status is not None:
            data["status"] = status
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def end(self, group_id: str, user_id: str, *, status: str) -> SessionRecord | None:
        """结束会话：标记终态并取消超时任务。"""
        key = (group_id, user_id)
        record = self.get(group_id, user_id)
        if record is None:
            return None
        self._cancel_timeout(key)
        data = record.to_db_dict()
        data["status"] = status
        updated = SessionRecord(**data)
        self._sessions[key] = updated
        return updated

    def set_muted(
        self,
        group_id: str,
        user_id: str,
        *,
        is_muted: bool,
    ) -> SessionRecord | None:
        """同步会话的禁言状态（由群禁言事件驱动）。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["is_muted"] = is_muted
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def remove_group(self, group_id: str) -> tuple[SessionRecord, ...]:
        """移除某群的全部会话并取消对应超时任务。

        用于机器人被移出群或群解散时清理该群遗留状态。

        Args:
            group_id: 群号。

        Returns:
            被移除的会话记录。

        """
        removed: list[SessionRecord] = []
        for key, record in list(self._sessions.items()):
            if key[0] == group_id:
                self._cancel_timeout(key)
                self._sessions.pop(key, None)
                removed.append(record)
        return tuple(removed)

    def remove(self, group_id: str, user_id: str) -> SessionRecord | None:
        """从内存移除会话（用于清理终态）。"""
        key = (group_id, user_id)
        self._cancel_timeout(key)
        return self._sessions.pop(key, None)

    def _schedule_timeout(self, key: _SessionKey) -> None:
        """为会话调度超时协程。"""
        if self._closed:
            return
        record = self._sessions[key]
        delay = max(0.0, (record.expires_at - datetime.now(UTC)).total_seconds())
        task_name = f"fanqie-timeout:{key[0]}:{key[1]}"
        task = asyncio.create_task(self._run_timeout(key, delay), name=task_name)
        self._timeout_tasks[key] = task

    def _cancel_timeout(self, key: _SessionKey) -> None:
        task = self._timeout_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def _run_timeout(self, key: _SessionKey, delay: float) -> None:
        """等待超时并触发回调（若会话仍处于 waiting）。"""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._timeout_tasks.pop(key, None)
        if not self.is_waiting(*key):
            return
        if self._timeout_callback is None:
            logger.warning("未配置超时回调，会话 {} 无法自动处理", key)
            return
        try:
            await self._timeout_callback(*key)
        except Exception:
            logger.exception("处理会话超时失败: {}", key)

    def close(self) -> None:
        """取消所有超时任务（停机时调用）。"""
        self._closed = True
        for task in self._timeout_tasks.values():
            if not task.done():
                task.cancel()
        self._timeout_tasks.clear()


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """返回全局会话存储单例。"""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def _reset_store() -> None:
    """重置全局会话存储（主要供测试使用）。"""
    global _store
    if _store is not None:
        _store.close()
    _store = None


__all__ = [
    "SessionRecord",
    "SessionStore",
    "get_session_store",
]
