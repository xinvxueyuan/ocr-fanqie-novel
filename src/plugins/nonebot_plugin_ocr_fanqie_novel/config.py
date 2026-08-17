"""NoneBot 解析的番茄读书入群验证插件配置。

配置项均通过环境变量（前缀 ``FANQIE_``）注入，由 NoneBot2 的
``get_plugin_config`` 统一解析。环境变量与字段名大小写不敏感，
例如 ``FANQIE_RESPONSE_TIMEOUT`` 对应 ``fanqie_response_timeout``。

"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """番茄读书入群验证插件配置。

    Attributes:
        fanqie_verify_groups: 需要执行入群验证的群号集合，空集合表示全部群。
        fanqie_admin_ids: 接收验证失败通知、可执行踢/保留命令的管理员 QQ 号集合。
        fanqie_welcome_message: 新成员入群后发送的验证提示消息。
        fanqie_response_timeout: 等待新成员发送截图的超时时间（秒），
            超时后按验证失败处理并通知管理员。
        fanqie_max_attempts: 识别失败的允许尝试次数，达上限后按验证
            失败处理并通知管理员。
        fanqie_admin_decision_timeout: 通知管理员后等待其决策的超时时间
            （秒），超时后将在群内通报并移出该成员。默认 16 小时。
        fanqie_review_max_times: 普通群成员（非群管理/群主）通过“重审”
            命令重新发起验证的最大次数，达到上限后需由管理员处理。
            管理员主动发起的重审不受此限制。默认 2 次。
        fanqie_notify_admin: 验证失败时是否私信通知管理员决定通过或踢出。
        fanqie_book_name_max_len: FR4 综合判断中有效书名的最大字符数。
        fanqie_ocr_api_url: PaddleOCR 云端 API 地址（留空使用官方默认服务）。
        fanqie_ocr_api_token: 调用云端 OCR API 的认证令牌。
        fanqie_ocr_timeout: 单次 HTTP 请求的超时时间（秒）。
        fanqie_ocr_poll_timeout: 等待 OCR 任务完成的总超时时间（秒）。
        fanqie_ocr_model: 使用的 PaddleOCR 模型名称（如 ``PP-OCRv6``）。
        fanqie_verification_policy_path: 放行策略的 TOML 配置文件路径，
            留空时使用 ``nonebot_plugin_localstore`` 的配置目录。策略决定
            书评详情页 OCR 识别出的哪些元素需要匹配、以及作者/书名白名单，
            可通过“重载番茄OCR配置”命令运行时重载。
        fanqie_message_store_enabled: 是否启用消息与审计记录存储。
        fanqie_message_store_summary_limit: 文本摘要的最大字符数。
        fanqie_message_store_cleanup_enabled: 是否在关闭时清理过期记录。
        fanqie_message_store_retention_days: 记录的保留天数。
        fanqie_message_store_record_api_calls: 是否记录平台 API 调用审计。

    """

    fanqie_verify_groups: set[int] = Field(default_factory=set)
    fanqie_admin_ids: set[int] = Field(default_factory=set)
    fanqie_welcome_message: str = (
        "欢迎加入本群！为了验证您是真实的读者，"
        "请发送一张您在番茄小说发布的「书评详情页」截图"
        "（需显示您的书评及「我」徽章）。谢谢配合！"
    )
    fanqie_response_timeout: int = 300
    fanqie_max_attempts: int = 3
    fanqie_admin_decision_timeout: int = 57600  # 16 小时（秒）
    fanqie_review_max_times: int = 2
    fanqie_notify_admin: bool = True
    fanqie_book_name_max_len: int = 100
    fanqie_ocr_api_url: str = ""
    fanqie_ocr_api_token: str = ""
    fanqie_ocr_timeout: float = 15.0
    fanqie_ocr_poll_timeout: float = 120.0
    fanqie_ocr_model: str = "PP-OCRv6"
    fanqie_verification_policy_path: str = ""
    fanqie_message_store_enabled: bool = True
    fanqie_message_store_summary_limit: int = 500
    fanqie_message_store_cleanup_enabled: bool = True
    fanqie_message_store_retention_days: int = 30
    fanqie_message_store_record_api_calls: bool = False
