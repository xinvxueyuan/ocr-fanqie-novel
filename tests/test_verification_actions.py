"""群管理动作边界守卫测试（踢出与成员信息查询）。"""

from __future__ import annotations

import time
from typing import Any

import pytest

from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.actions import (
    MemberInfo,
    get_member_info,
    kick_member,
)


class FakeBot:
    """模拟 OneBot11 Bot：可配置成员是否在群、是否禁言。"""

    self_id = "bot1"

    def __init__(self, *, in_group: bool = True, shut_up_timestamp: int = 0) -> None:
        self.in_group = in_group
        self.shut_up_timestamp = shut_up_timestamp
        self.calls: list[Any] = []

    async def get_group_member_info(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_group_member_info", kwargs))
        if not self.in_group:
            from nonebot.adapters.onebot.v11.exception import ActionFailed

            raise ActionFailed(retcode=100, retmsg="member not found", data=None)
        return {
            "user_id": kwargs["user_id"],
            "role": "member",
            "card": "",
            "nickname": "某用户",
            "shut_up_timestamp": self.shut_up_timestamp,
        }

    async def set_group_kick(self, **kwargs: Any) -> None:
        self.calls.append(("set_group_kick", kwargs))


def _member(user_id: int = 10001, *, is_muted: bool = False) -> MemberInfo:
    future = int(time.time()) + 3600
    return MemberInfo(
        user_id=user_id,
        role="member",
        card="",
        nickname="某用户",
        shut_up_timestamp=future if is_muted else 0,
    )


def test_member_info_is_muted() -> None:
    assert _member().is_muted is False
    assert _member(is_muted=True).is_muted is True
    assert MemberInfo(1, "member", "", "", 0).display_name == "1"
    assert MemberInfo(1, "member", "卡片", "昵称", 0).display_name == "卡片"
    assert MemberInfo(1, "member", "", "昵称", 0).display_name == "昵称"


@pytest.mark.asyncio
async def test_get_member_info_in_group() -> None:
    bot: Any = FakeBot()
    info = await get_member_info(bot, 123, 10001)
    assert info is not None
    assert info.user_id == 10001
    assert info.is_muted is False


@pytest.mark.asyncio
async def test_get_member_info_not_in_group() -> None:
    bot: Any = FakeBot(in_group=False)
    info = await get_member_info(bot, 123, 10001)
    assert info is None


@pytest.mark.asyncio
async def test_kick_member_in_group() -> None:
    bot: Any = FakeBot()
    result = await kick_member(bot, 123, 10001)
    assert result is True
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert len(kicks) == 1


@pytest.mark.asyncio
async def test_kick_member_not_in_group_is_noop_success() -> None:
    bot: Any = FakeBot(in_group=False)
    result = await kick_member(bot, 123, 10001)
    assert result is True
    assert not any(c[0] == "set_group_kick" for c in bot.calls)


@pytest.mark.asyncio
async def test_actions_accept_passed_member() -> None:
    """预取 member 应避免重复查询。"""
    bot: Any = FakeBot()
    member = _member()
    await kick_member(bot, 123, 10001, member)
    get_calls = [c for c in bot.calls if c[0] == "get_group_member_info"]
    assert get_calls == []


def test_decorate_notice_includes_group_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员通知应附加群作者白名单与群内决策指引。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.actions import (
        _decorate_notice,
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
                    authors=(
                        AuthorEntry(name="阿百川大鬼"),
                        AuthorEntry(name="刘慈欣"),
                    ),
                ),
            },
        ),
    )

    notice = _decorate_notice("【验证失败】测试", 123, 10001)

    assert "该群允许作者：刘慈欣, 阿百川大鬼" in notice
    assert "请在群内执行：/kick 10001 或 /keep 10001" in notice
    assert "16 小时内未处理" in notice


def test_decorate_notice_unconfigured_group() -> None:
    """群未配置作者白名单时提示未配置。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.actions import (
        _decorate_notice,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        VerificationPolicy,
    )

    policy_module._policy_cache = VerificationPolicy(
        require_all=False,
        required_elements=frozenset({"book_name", "author"}),
    )

    notice = _decorate_notice("【验证失败】测试", 999, 10001)

    assert "该群允许作者：未配置" in notice
