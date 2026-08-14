"""OneBot V11 适配器处理器注册（参照对象项目的 selected_adapter_handle 模式）。"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import (
    Bot as OneBot11Bot,
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
)
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ......handle.qq.commands.verification import (
    group_admin_change,
    group_ban,
    group_decrease,
    group_increase,
    image_submission,
    keep_cmd,
    kick_cmd,
    reload_config_cmd,
)
from ......services.verification import (
    PolicyConfigError,
    admin_decision,
    get_session_store,
    handle_submission,
    reload_policy,
    start_verification,
)


def _register[T: Callable[..., Awaitable[Any]]](
    matcher: type[Matcher],
) -> Callable[[T], T]:
    """返回注册装饰器，把处理函数挂到给定 matcher（本插件仅支持 onebot.v11）。"""

    def decorator(func: T) -> T:
        matcher.handle()(wrapped(func))
        return func

    return decorator


def wrapped[T: Callable[..., Awaitable[Any]]](func: T) -> T:
    """保留函数签名并记录处理异常。"""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception:
            logger.exception("验证处理器异常: %s", func.__name__)
            return None

    return wrapper  # type: ignore[return-value]


def _image_url(event: GroupMessageEvent) -> str | None:
    """从群消息中提取首张图片的 URL；无 URL 时回退到 file 字段。"""
    for segment in event.message:
        if segment.type != "image":
            continue
        data = segment.data
        url = data.get("url")
        if url:
            return str(url)
        file = data.get("file")
        if file:
            return f"file://{file}" if not file.startswith("file://") else str(file)
    return None


@_register(group_increase)
async def on_group_increase(
    bot: OneBot11Bot,
    event: GroupIncreaseNoticeEvent,
) -> None:
    """FR1：新成员入群，开启验证流程。"""
    await start_verification(bot, group_id=event.group_id, user_id=event.user_id)


@_register(group_decrease)
async def on_group_decrease(
    bot: OneBot11Bot,
    event: GroupDecreaseNoticeEvent,
) -> None:
    """PRD 10：成员退群时清除遗留会话。

    边界守卫：若机器人在该群被移出（``sub_type == kick_me``），清理
    该群全部会话；否则仅清理离开成员的会话。

    """
    _ = bot
    store = get_session_store()
    if event.sub_type == "kick_me":
        removed = store.remove_group(str(event.group_id))
        if removed:
            logger.info(
                "机器人在群 %s 被移出，清理 %s 个会话",
                event.group_id,
                len(removed),
            )
        return
    record = store.remove(str(event.group_id), str(event.user_id))
    if record is not None:
        logger.info("成员退群，清除验证会话: %s", (event.group_id, event.user_id))


@_register(group_admin_change)
async def on_group_admin_change(
    event: GroupAdminNoticeEvent,
) -> None:
    """群管理员变动：被设为管理员的新成员不再需要验证。"""
    store = get_session_store()
    record = store.get(str(event.group_id), str(event.user_id))
    if record is None or record.status != "waiting":
        return
    if event.sub_type == "set":
        store.end(str(event.group_id), str(event.user_id), status="approved")
        logger.info(
            "成员 %s 在群 %s 被设为管理员，直接放行",
            event.user_id,
            event.group_id,
        )


@_register(group_ban)
async def on_group_ban(
    bot: OneBot11Bot,
    event: GroupBanNoticeEvent,
) -> None:
    """群禁言事件：同步会话禁言状态；超级用户在监控群被禁言则自动解禁。"""
    store = get_session_store()

    if event.sub_type == "ban":
        if _is_superuser(int(event.user_id)) and (
            await _auto_unmute_superuser(bot, event)
        ):
            return
        record = store.get(str(event.group_id), str(event.user_id))
        if record is not None:
            store.set_muted(str(event.group_id), str(event.user_id), is_muted=True)
            logger.debug(
                "成员 %s 在群 %s 被禁言（时长 %s 秒）",
                event.user_id,
                event.group_id,
                event.duration,
            )
    elif event.sub_type == "lift_ban":
        record = store.get(str(event.group_id), str(event.user_id))
        if record is not None:
            store.set_muted(str(event.group_id), str(event.user_id), is_muted=False)


def _is_superuser(user_id: int) -> bool:
    """是否为配置的超级用户。"""
    from nonebot import get_driver

    return str(user_id) in get_driver().config.superusers


async def _auto_unmute_superuser(
    bot: OneBot11Bot,
    event: GroupBanNoticeEvent,
) -> bool:
    """超级用户在监控群被禁言时自动解禁；返回是否已处理。"""
    from ......services.verification import get_policy

    if not get_policy().should_monitor_group(event.group_id):
        return False
    try:
        await bot.set_group_ban(
            group_id=event.group_id,
            user_id=event.user_id,
            duration=0,
        )
    except Exception:
        logger.exception(
            "自动解禁超级用户失败 group=%s user=%s",
            event.group_id,
            event.user_id,
        )
        return False
    logger.info(
        "超级用户 %s 在监控群 %s 被禁言，已自动解禁",
        event.user_id,
        event.group_id,
    )
    return True


@_register(image_submission)
async def on_image_submission(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """FR2：处理待验证成员的阅读截图。

    由于不使用自定义 Rule（见 commands 模块注释），此处自行判断
    消息来源是否处于待验证状态且包含图片；不满足时直接返回。

    """
    from ......handle.qq.commands.verification import (
        _contains_image,
        _has_pending_session,
    )

    if not _has_pending_session(event) or not _contains_image(event):
        return
    reply = await handle_submission(
        bot,
        group_id=event.group_id,
        user_id=event.user_id,
        image_url=_image_url(event),
    )
    message = MessageSegment.at(event.user_id) + f" {reply}"
    await bot.send_group_msg(group_id=event.group_id, message=message)


@_register(kick_cmd)
async def on_admin_kick(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """FR9：管理员踢出指定成员。"""
    if not _is_admin_user(event):
        return
    target_user_id = _extract_target_user(args, event)
    if target_user_id is None:
        hint = MessageSegment.at(event.user_id) + (
            " 请提供成员 QQ 号，例如：/kick 123456"
        )
        await bot.send_group_msg(group_id=event.group_id, message=hint)
        return
    reply = await admin_decision(
        bot,
        group_id=event.group_id,
        user_id=target_user_id,
        keep=False,
    )
    await bot.send_group_msg(group_id=event.group_id, message=reply)


@_register(keep_cmd)
async def on_admin_keep(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """FR9：管理员保留指定成员。"""
    if not _is_admin_user(event):
        return
    target_user_id = _extract_target_user(args, event)
    if target_user_id is None:
        hint = MessageSegment.at(event.user_id) + (
            " 请提供成员 QQ 号，例如：/keep 123456"
        )
        await bot.send_group_msg(group_id=event.group_id, message=hint)
        return
    reply = await admin_decision(
        bot,
        group_id=event.group_id,
        user_id=target_user_id,
        keep=True,
    )
    await bot.send_group_msg(group_id=event.group_id, message=reply)


def _is_admin_user(event: GroupMessageEvent) -> bool:
    """命令发起者是否为配置的管理员。"""
    from ......core.config import plugin_config

    try:
        return int(event.user_id) in plugin_config.fanqie_admin_ids
    except (TypeError, ValueError):
        return False


@_register(reload_config_cmd)
async def on_reload_config(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """重载番茄 OCR 配置：放行策略 TOML 运行时热更新。"""
    if not _is_admin_user(event):
        return
    try:
        policy = reload_policy()
    except PolicyConfigError as exc:
        message = MessageSegment.at(event.user_id) + f" 配置重载失败：{exc}"
        await bot.send_group_msg(group_id=event.group_id, message=message)
        return

    mode = "全部元素" if policy.require_all else "指定元素"
    total_authors = sum(len(group.authors) for group in policy.groups.values())
    summary = (
        f"番茄 OCR 配置已重载：放行模式={mode}，"
        f"监控群={len(policy.groups)} 个，"
        f"作者白名单={total_authors} 人。"
    )
    message = MessageSegment.at(event.user_id) + f" {summary}"
    await bot.send_group_msg(group_id=event.group_id, message=message)


def _extract_target_user(args: Message, event: GroupMessageEvent) -> int | None:
    """从命令参数或 @ 中解析目标成员 QQ 号。"""
    text = args.extract_plain_text().strip()
    if text:
        try:
            return int(text)
        except ValueError:
            return None
    for segment in event.message:
        if segment.type == "at":
            qq = segment.data.get("qq")
            if qq is not None and qq != "all":
                try:
                    return int(qq)
                except (TypeError, ValueError):
                    return None
    return None
