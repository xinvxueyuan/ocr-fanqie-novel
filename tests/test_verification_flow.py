"""入群验证流程编排测试（FR1/FR2/FR5/FR6/FR7/FR8/FR9）。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
    admin_decision,
    flow as flow_module,
    get_session_store,
    handle_submission,
    handle_timeout,
    start_verification,
)


class FakeBot:
    """最小 OneBot11 Bot 替身。"""

    self_id = "bot1"

    def __init__(self, *, muted_until: int = 0, in_group: bool = True) -> None:
        self.calls: list[Any] = []
        self.muted_until = muted_until
        self.in_group = in_group

    async def send_group_msg(self, **kwargs: Any) -> None:
        self.calls.append(("send_group_msg", kwargs))

    async def set_group_kick(self, **kwargs: Any) -> None:
        self.calls.append(("set_group_kick", kwargs))

    async def set_group_ban(self, **kwargs: Any) -> None:
        self.calls.append(("set_group_ban", kwargs))

    async def send_private_msg(self, **kwargs: Any) -> None:
        self.calls.append(("send_private_msg", kwargs))

    async def get_group_member_info(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_group_member_info", kwargs))
        if not self.in_group:
            from nonebot.adapters.onebot.v11.exception import ActionFailed

            raise ActionFailed(retcode=100, retmsg="member not found", data=None)
        return {
            "user_id": kwargs.get("user_id", 0),
            "role": "member",
            "card": "",
            "nickname": "某用户",
            "shut_up_timestamp": self.muted_until,
        }

    async def call_api(self, api: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("call_api", api, kwargs))
        return {"card": "某用户", "nickname": "某用户"}


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


@pytest.fixture(autouse=True)
def _lenient_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让测试群 123 通过策略检查（配置作者节点，作者不在白名单约束）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )


@pytest.mark.asyncio
async def test_start_verification_sends_guide() -> None:
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    assert get_session_store().is_waiting("123", "10001") is True
    send_calls = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert len(send_calls) == 1
    assert "欢迎加入本群" in str(send_calls[0][1]["message"])


@pytest.mark.asyncio
async def test_start_verification_skips_non_monitored_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """群不在监控范围（未配置群节点）时应跳过验证，不建会话不发引导。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                456: GroupPolicy(
                    group_id=456,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )

    bot: Any = FakeBot()
    record = await start_verification(bot, group_id=123, user_id=10001)

    assert record is None
    assert get_session_store().is_waiting("123", "10001") is False
    send_calls = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert send_calls == []


@pytest.mark.asyncio
async def test_handle_submission_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有效截图应通过验证并发送欢迎消息。"""

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_with_evidence()

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "验证通过" in reply
    assert get_session_store().get("123", "10001").status == "approved"  # type: ignore[union-attr]
    welcomes = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert any("欢迎加入本群" in str(c[1]["message"]) for c in welcomes)


@pytest.mark.asyncio
async def test_handle_submission_insufficient_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """信息不足应提示重试并计数，不直接踢出。"""

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_empty()

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/blank.png",
    )

    assert "剩余尝试次数" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.retry_count == 1
    assert record.status == "waiting"


@pytest.mark.asyncio
async def test_handle_submission_other_review_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """他人书评（无「我」徽章）应视为信息不足并重试。"""

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_other_review()

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/other.png",
    )

    assert "剩余尝试次数" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.retry_count == 1
    assert record.status == "waiting"


@pytest.mark.asyncio
async def test_handle_submission_reject_notifies_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定拒绝应结束会话并私信管理员决策（不禁言）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(plugin_config, "fanqie_admin_ids", {90001})
    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", True)
    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="张三"),),
                ),
            },
        ),
    )

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_with_author("李四")

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/bad.png",
    )

    assert "验证未通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "rejected"
    bans = [c for c in bot.calls if c[0] == "set_group_ban"]
    assert bans == []
    privates = [c for c in bot.calls if c[0] == "send_private_msg"]
    assert any("验证失败" in str(c[1]["message"]) for c in privates)
    assert any("/keep" in str(c[1]["message"]) for c in privates)


@pytest.mark.asyncio
async def test_handle_submission_policy_rejects_missing_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """策略要求指定元素但截图缺少时应拒绝。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author", "rating"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_with_evidence()

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "验证未通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "rejected"


@pytest.mark.asyncio
async def test_handle_submission_policy_rejects_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作者不在白名单时应拒绝。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="张三"),),
                ),
            },
        ),
    )

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_with_author("李四")

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "验证未通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "rejected"


@pytest.mark.asyncio
async def test_handle_timeout_notifies_and_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时应结束会话并私信管理员决策（不自动踢出）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_admin_ids", {90001})
    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", True)

    bot: Any = FakeBot()

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)

    await handle_timeout("123", "10001")

    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    privates = [c for c in bot.calls if c[0] == "send_private_msg"]
    assert len(privates) == 1
    assert "/keep" in str(privates[0][1]["message"])
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "expired"


@pytest.mark.asyncio
async def test_admin_kick_decision() -> None:
    """管理员 /kick 应踢出并结束会话。"""
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await admin_decision(bot, group_id=123, user_id=10001, keep=False)

    assert "已将该成员移出群聊" in reply
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert len(kicks) == 1
    assert get_session_store().get("123", "10001").status == "kicked"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_admin_keep_decision() -> None:
    """管理员 /keep 应通过并保留成员。"""
    bot: Any = FakeBot()
    record = await start_verification(bot, group_id=123, user_id=10001)
    assert record is not None
    store = get_session_store()

    reply = await admin_decision(bot, group_id=123, user_id=10001, keep=True)

    assert "已保留" in reply
    assert store.get("123", "10001").status == "approved"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_handle_timeout_member_already_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员已退群时，超时处理应跳过动作并结束会话。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", False)

    bot: Any = FakeBot(in_group=False)

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    get_session_store().start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    await handle_timeout("123", "10001")

    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "expired"


@pytest.mark.asyncio
async def test_handle_submission_member_already_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员已退群时，提交截图应直接结束会话。"""

    async def fake_recognize(url: str) -> Any:  # noqa: ARG001
        return _ocr_result_with_evidence()

    monkeypatch.setattr(flow_module, "recognize_image_url", fake_recognize)

    bot: Any = FakeBot(in_group=False)
    get_session_store().start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "不在群聊" in reply
    assert get_session_store().get("123", "10001") is None


@pytest.mark.asyncio
async def test_handle_submission_member_muted_state_synced() -> None:
    """成员已处于禁言时，会话应同步 is_muted。"""
    future = int(__import__("time").time()) + 3600
    bot: Any = FakeBot(muted_until=future)
    await start_verification(bot, group_id=123, user_id=10001)

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.is_muted is True


def _ocr_result_with_evidence() -> Any:
    """构造能提取出有效证据的书评详情页 OCR 结果（本人发布）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-pass",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                    OCRTextLine(text="新v学员", confidence=0.98, box=_box(30)),
                    OCRTextLine(text="我", confidence=0.97, box=_box(40)),
                    OCRTextLine(text="刚刚", confidence=0.96, box=_box(60)),
                    OCRTextLine(text="阅读2小时后点评", confidence=0.95, box=_box(80)),
                    OCRTextLine(
                        text="综漫：吉他雇佣兵无法找到归宿？",
                        confidence=0.98,
                        box=_box(200),
                    ),
                    OCRTextLine(text="阿百川大鬼", confidence=0.97, box=_box(220)),
                ]
            )
        ],
    )


def _ocr_result_with_author(author: str) -> Any:
    """构造带指定作者的书评详情页 OCR 结果。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-author",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                    OCRTextLine(text="新v学员", confidence=0.98, box=_box(30)),
                    OCRTextLine(text="我", confidence=0.97, box=_box(40)),
                    OCRTextLine(text="刚刚", confidence=0.96, box=_box(60)),
                    OCRTextLine(text="阅读2小时后点评", confidence=0.95, box=_box(80)),
                    OCRTextLine(
                        text="综漫：吉他雇佣兵无法找到归宿？",
                        confidence=0.98,
                        box=_box(200),
                    ),
                    OCRTextLine(text=author, confidence=0.97, box=_box(220)),
                ]
            )
        ],
    )


def _ocr_result_empty() -> Any:
    """构造无有效信息的 OCR 结果（无「我」徽章）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-empty",
        pages=[OCRPage(lines=[OCRTextLine(text="首页", confidence=0.9, box=_box(0))])],
    )


def _ocr_result_other_review() -> Any:
    """构造他人书评的 OCR 结果（无「我」徽章）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-other",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                    OCRTextLine(text="叶月辉夜", confidence=0.98, box=_box(30)),
                    OCRTextLine(text="05-28", confidence=0.97, box=_box(60)),
                    OCRTextLine(text="阅读11小时后点评", confidence=0.96, box=_box(80)),
                    OCRTextLine(
                        text="综漫：吉他雇佣兵无法找到归宿？",
                        confidence=0.98,
                        box=_box(200),
                    ),
                    OCRTextLine(text="阿百川大鬼", confidence=0.97, box=_box(220)),
                ]
            )
        ],
    )


def _box(y: int) -> list[list[int]]:
    return [[0, y], [100, y], [100, y + 20], [0, y + 20]]
