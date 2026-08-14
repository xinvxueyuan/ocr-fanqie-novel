"""机器人连接/断开钩子处理。"""

from __future__ import annotations

from nonebot import get_driver
from nonebot.adapters import Bot

from ...core.async_utils import fire_and_forget
from ...services.message_store import record_bot_lifecycle

driver = get_driver()


@driver.on_bot_connect
async def on_bot_connect(bot: Bot) -> None:
    """机器人连接时记录生命周期事件。"""
    fire_and_forget(
        record_bot_lifecycle(bot, "bot_connected"), name="record_bot_lifecycle"
    )


@driver.on_bot_disconnect
async def on_bot_disconnect(bot: Bot) -> None:
    """机器人断开时记录生命周期事件。"""
    fire_and_forget(
        record_bot_lifecycle(bot, "bot_disconnected"), name="record_bot_lifecycle"
    )
