"""QQ 平台命令定义与事件响应器。"""

from nonebot import on_command, on_message, on_notice, on_type
from nonebot.adapters.onebot.v11 import (
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
    GroupDecreaseNoticeEvent,
)
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.permission import SUPERUSER

from ....services.verification import get_session_store


def _has_pending_session(event: GroupMessageEvent) -> bool:
    """事件是否来自有待验证会话的成员。"""
    return get_session_store().is_waiting(
        str(event.group_id),
        str(event.user_id),
    )


def _contains_image(event: GroupMessageEvent) -> bool:
    """消息是否包含图片消息段。"""
    return any(segment.type == "image" for segment in event.message)


# FR1：新人入群。低优先级、不 block，确保其他插件也能响应群增事件。
group_increase = on_notice(priority=1, block=False)

# 成员退群：清理遗留会话。
group_decrease = on_type(GroupDecreaseNoticeEvent, priority=1, block=False)

# 群管理员变动：用于边界守卫（成员被提升管理员后不再需要验证）。
group_admin_change = on_type(GroupAdminNoticeEvent, priority=1, block=False)

# 群禁言事件：同步会话的禁言状态，防止重复禁言。
group_ban = on_type(GroupBanNoticeEvent, priority=1, block=False)

# FR2：待验证成员的图片提交。
# 注意：不使用自定义 Rule（避免 NoneBot 依赖注入对 DependencyCache 的
# TypeAdapter 构建问题），改为在处理器内自行判断。
image_submission = on_message(priority=5, block=False)

# FR9：管理员决策命令。
kick_cmd = on_command(
    "kick",
    aliases={"踢出", "踢"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
keep_cmd = on_command(
    "keep",
    aliases={"保留", "留"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 配置热重载：管理员手动触发重新加载策略与提取规则 TOML。
reload_config_cmd = on_command(
    "重载番茄OCR配置",
    aliases={"重载OCR配置", "重载配置"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


__all__ = [
    "group_admin_change",
    "group_ban",
    "group_decrease",
    "group_increase",
    "image_submission",
    "keep_cmd",
    "kick_cmd",
    "reload_config_cmd",
]
