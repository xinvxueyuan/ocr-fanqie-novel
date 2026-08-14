"""入群验证流程编排（FR1/FR2/FR5/FR6/FR7/FR8）。

把 OCR 服务层、FR3 信息提取、FR4 综合判断、会话管理与群管理动作
串成一个完整流程。本模块只接收规范化参数，不直接依赖 NoneBot
matcher，便于测试。

"""
from __future__ import annotations

from typing import TYPE_CHECKING

from nonebot import logger, require

require("nonebot_plugin_orm")
from nonebot.adapters.onebot.v11 import Bot
from nonebot_plugin_orm import get_session

from ...core.config import plugin_config
from ...repositories import message_store as repository
from ...services.ocr import (
    OCRError,
    recognize_image_url,
)
from . import actions, extractor, judgment, policy
from .session import SessionRecord, get_session_store

if TYPE_CHECKING:
    from .models import ReadingEvidence


def _to_dict(evidence: ReadingEvidence) -> dict:
    """把提取结果转换为可入库的 last_extracted 字典。"""
    return {
        "is_self_review": evidence.is_self_review,
        "reader_name": evidence.reader_name.value if evidence.reader_name else None,
        "book_name": evidence.book_name.value if evidence.book_name else None,
        "author": evidence.author.value if evidence.author else None,
        "rating": evidence.rating.value if evidence.rating else None,
        "publish_time": (
            evidence.publish_time.value if evidence.publish_time else None
        ),
        "read_duration": (
            evidence.read_duration.value if evidence.read_duration else None
        ),
        "review_text": evidence.review_text.value if evidence.review_text else None,
    }


async def start_verification(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
) -> SessionRecord | None:
    """FR1：为新成员开启验证会话并发送引导消息。

    若策略配置了监控群聊白名单且该群不在其中，则跳过本次验证。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。

    Returns:
        创建的会话记录；未启用验证时返回 ``None``。

    """
    from ...hooks.adapters import resolve_platform_context

    if not policy.get_policy().should_monitor_group(group_id):
        logger.info(
            "群 {} 不在监控范围，跳过新成员 {} 的验证",
            group_id,
            user_id,
        )
        return None

    platform_context = resolve_platform_context(bot)
    record = get_session_store().start(
        group_id=str(group_id),
        user_id=str(user_id),
        bot_id=str(getattr(bot, "self_id", "")),
        platform_id=platform_context.platform_id if platform_context else "qq",
        adapter_id=platform_context.adapter_id if platform_context else "~onebot.v11",
        protocol_id=platform_context.protocol_id if platform_context else None,
    )

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info("引导前检查：成员 {} 已不在群 {} 中，结束会话", user_id, group_id)
        get_session_store().remove(str(group_id), str(user_id))
        return None
    if member.is_muted:
        get_session_store().set_muted(str(group_id), str(user_id), is_muted=True)

    await _persist_session(record)
    await actions.send_guide(bot, group_id, user_id)
    logger.info(
        "新成员 {} 进入群 {} 验证流程，截止 {}",
        user_id,
        group_id,
        record.expires_at.isoformat(),
    )
    return record


async def handle_submission(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
    image_url: str | None,
) -> str:
    """FR2~FR8：处理新成员提交的阅读截图。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。
        image_url: 图片 URL（无可用 URL 时视为下载失败）。

    Returns:
        面向用户的反馈消息。

    """
    store = get_session_store()
    record = store.get(str(group_id), str(user_id))
    if record is None or record.status != "waiting":
        return "当前没有待处理的验证请求。"

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info("提交处理：成员 {} 已不在群 {} 中，结束会话", user_id, group_id)
        store.remove(str(group_id), str(user_id))
        return "你已不在群聊中，无需验证。"
    if member.is_muted and not record.is_muted:
        store.set_muted(str(group_id), str(user_id), is_muted=True)

    if not image_url:
        return await _handle_download_failure(group_id, user_id)

    try:
        result = await recognize_image_url(image_url)
    except OCRError as exc:
        logger.warning("OCR 识别失败 group={} user={}: {}", group_id, user_id, exc)
        return await _handle_ocr_failure(bot, group_id, user_id)

    evidence = extractor.extract_reading_evidence(result)
    await _persist_last_extracted(record, evidence)

    if not evidence.is_sufficient:
        logger.info("信息不足 group={} user={}", group_id, user_id)
        return await _handle_insufficient(bot, group_id, user_id)

    policy_check = policy.get_policy().check(evidence, group_id)
    verdict = judgment.judge_evidence(evidence)
    reject_reason = policy_check.reason if not policy_check.passed else verdict.reason

    if reject_reason is None:
        logger.info("验证通过 group={} user={}", group_id, user_id)
        return await _handle_pass(bot, group_id, user_id)

    logger.info(
        "验证拒绝 group={} user={} reason={}",
        group_id,
        user_id,
        reject_reason,
    )
    return await _handle_reject(bot, group_id, user_id, evidence, reject_reason)


async def handle_timeout(group_id: str, user_id: str) -> None:
    """FR7：超时未收到截图，按验证失败处理并通知管理员决策。"""
    store = get_session_store()
    record = store.get(group_id, user_id)
    if record is None:
        return
    bot_id = record.bot_id
    if not bot_id:
        logger.warning("会话缺少 bot_id，跳过超时处理: {}", (group_id, user_id))
        return
    bot = await _get_bot(bot_id)
    if bot is None:
        logger.warning("找不到 Bot {}，跳过超时处理", bot_id)
        return

    member = await actions.get_member_info(bot, int(group_id), int(user_id))
    if member is None:
        logger.info("超时处理：成员 {} 已不在群 {} 中，直接结束", user_id, group_id)
        await _persist_session(store.end(group_id, user_id, status="expired"))
        await actions.notify_admins(
            bot,
            group_id=int(group_id),
            user_id=int(user_id),
            event=None,
            message=f"用户 {user_id} 在群 {group_id} 超时未提供截图，且已不在群聊中。",
        )
        return

    await _persist_session(store.end(group_id, user_id, status="expired"))
    await actions.notify_admins(
        bot,
        group_id=int(group_id),
        user_id=int(user_id),
        event=None,
        message=f"用户 {user_id} 在群 {group_id} 超时未提供截图。",
    )


async def admin_decision(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
    keep: bool,
) -> str:
    """FR9：管理员决定踢出或保留。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 目标成员 QQ 号。
        keep: ``True`` 表示保留（/keep），``False`` 表示踢出（/kick）。

    Returns:
        面向管理员的反馈消息。

    """
    store = get_session_store()
    if keep:
        store.end(str(group_id), str(user_id), status="approved")
        await actions.send_welcome(bot, group_id, user_id)
        return "已保留该成员并通过验证。"
    member = await actions.get_member_info(bot, group_id, user_id)
    kicked = await actions.kick_member(bot, group_id, user_id, member)
    store.end(str(group_id), str(user_id), status="kicked")
    if member is None:
        return "该成员已不在群聊中。"
    return "已将该成员移出群聊。" if kicked else "踢出失败，请检查机器人权限。"


async def _handle_ocr_failure(bot: Bot, group_id: int, user_id: int) -> str:
    """FR8：OCR 调用失败，重试计数 +1。"""
    return await _increment_retry(bot, group_id, user_id, kind="识别失败")


async def _handle_insufficient(
    bot: Bot,
    group_id: int,
    user_id: int,
) -> str:
    """FR8：信息不足，重试计数 +1。"""
    return await _increment_retry(bot, group_id, user_id, kind="信息不足")


async def _handle_download_failure(group_id: int, user_id: int) -> str:
    """PRD 10：图片下载失败不计入识别失败，提示重新发送。"""
    store = get_session_store()
    await _persist_session(store.get(str(group_id), str(user_id)))
    return "图片获取失败，请重新发送清晰的截图。"


async def _increment_retry(
    bot: Bot,
    group_id: int,
    user_id: int,
    *,
    kind: str,
) -> str:
    """识别失败：计数 +1，达上限则踢出（FR8）。"""
    store = get_session_store()
    updated = store.mark_retry(str(group_id), str(user_id))
    if updated is None:
        return "当前没有待处理的验证请求。"
    await _persist_session(updated)

    remaining = plugin_config.fanqie_max_attempts - updated.retry_count
    if remaining > 0:
        return f"{kind}，请重新发送清晰的截图（剩余尝试次数：{remaining}）。"

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info("重试耗尽：成员 {} 已不在群 {} 中，直接结束", user_id, group_id)
        await _persist_session(store.end(str(group_id), str(user_id), status="failed"))
        await actions.notify_admins(
            bot,
            group_id=group_id,
            user_id=user_id,
            event=None,
            message=(
                f"用户 {user_id} 在群 {group_id} 连续 "
                f"{plugin_config.fanqie_max_attempts} 次识别失败，但已不在群聊中。"
            ),
        )
        return (
            f"连续 {plugin_config.fanqie_max_attempts} 次识别失败，"
            "已通知管理员处理。"
        )

    await _persist_session(store.end(str(group_id), str(user_id), status="failed"))
    await actions.notify_admins(
        bot,
        group_id=group_id,
        user_id=user_id,
        event=None,
        message=(
            f"用户 {user_id} 在群 {group_id} 连续 "
            f"{plugin_config.fanqie_max_attempts} 次识别失败。"
        ),
    )
    return f"连续 {plugin_config.fanqie_max_attempts} 次识别失败，已通知管理员处理。"


async def _handle_pass(
    bot: Bot,
    group_id: int,
    user_id: int,
) -> str:
    """FR5：通过验证，发送欢迎消息。"""
    store = get_session_store()

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info("放行处理：成员 {} 已不在群 {} 中，仅结束会话", user_id, group_id)
        await _persist_session(
            store.end(str(group_id), str(user_id), status="approved")
        )
        return "验证通过。"

    await _persist_session(store.end(str(group_id), str(user_id), status="approved"))
    return "验证通过，欢迎加入本群！"


async def _handle_reject(
    bot: Bot,
    group_id: int,
    user_id: int,
    evidence: ReadingEvidence,
    reason: str | None,
) -> str:
    """FR6：拒绝验证，结束会话并通知管理员决策。"""
    store = get_session_store()

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info("拒绝处理：成员 {} 已不在群 {} 中，仅结束会话", user_id, group_id)
        await _persist_session(
            store.end(str(group_id), str(user_id), status="rejected")
        )
        await actions.notify_admins(
            bot,
            group_id=group_id,
            user_id=user_id,
            event=None,
            message=(
                f"用户 {user_id} 在群 {group_id} 未通过验证（{reason}），"
                "但已不在群聊中。"
            ),
        )
        return "验证未通过。"

    await _persist_session(
        store.update_last_extracted(
            str(group_id),
            str(user_id),
            _to_dict(evidence),
            status="rejected",
        )
    )
    await actions.notify_admins(
        bot,
        group_id=group_id,
        user_id=user_id,
        event=None,
        message=actions.build_admin_notice(
            group_id=group_id,
            user_id=user_id,
            reader_name=evidence.reader_name.value if evidence.reader_name else None,
            book_name=evidence.book_name.value if evidence.book_name else None,
            author=evidence.author.value if evidence.author else None,
            rating=evidence.rating.value if evidence.rating else None,
            publish_time=evidence.publish_time.value if evidence.publish_time else None,
        ),
    )
    return "验证未通过，已通知管理员处理。"


async def _persist_session(record: SessionRecord | None) -> None:
    """把会话记录写入数据库（尽力而为）。"""
    if record is None or not plugin_config.fanqie_message_store_enabled:
        return
    try:
        async with get_session() as session:
            await repository.upsert_verification_session(
                session,
                repository.VerificationSessionWrite(
                    platform_id=record.platform_id,
                    adapter_id=record.adapter_id,
                    protocol_id=record.protocol_id,
                    bot_id=record.bot_id,
                    group_id=record.group_id,
                    user_id=record.user_id,
                ),
                status=record.status,
                retry_count=record.retry_count,
                is_muted=record.is_muted,
                last_extracted=record.last_extracted,
                trigger_time=record.trigger_time,
                expires_at=record.expires_at,
            )
    except Exception:  # noqa: BLE001 - 持久化失败不阻断主流程
        logger.exception("持久化验证会话失败: {}", (record.group_id, record.user_id))


async def _persist_last_extracted(
    record: SessionRecord,
    evidence: ReadingEvidence,
) -> None:
    """把最近一次提取结果写入会话。"""
    store = get_session_store()
    updated = store.update_last_extracted(
        record.group_id,
        record.user_id,
        _to_dict(evidence),
    )
    await _persist_session(updated)


async def _get_bot(bot_id: str) -> Bot | None:
    """按 self_id 查找已连接 OneBot11 Bot 实例。"""
    from nonebot import get_bots

    found = get_bots().get(bot_id)
    return found if isinstance(found, Bot) else None


__all__ = [
    "admin_decision",
    "handle_submission",
    "handle_timeout",
    "start_verification",
]
