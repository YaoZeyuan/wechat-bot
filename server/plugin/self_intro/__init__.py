"""Reply when a group message contains only the assistant mention."""

PLUGIN_ID = "self_intro"
METADATA = {
    "name": "自我介绍",
    "description": "整条消息仅为 @助手名字 时，发送配置的介绍文案",
    "default_enabled": False,
    "version": "1.0.0",
    "capability": "手机发送",
    "fields": [
        {"key": "assistant_name", "label": "助手名字", "type": "input", "max_length": 80,
         "placeholder": "张八旦", "description": "填写名字即可。群内单独发送 @名字 时触发。"},
        {"key": "intro_reply", "label": "介绍文案", "type": "textarea", "max_length": 2500},
    ],
}


def match(text: str, name: str) -> bool:
    return bool(name) and text.strip() == f"@{name}"
