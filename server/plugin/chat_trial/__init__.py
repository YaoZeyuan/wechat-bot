PLUGIN_ID = "chat_trial"
METADATA = {
    "name": "审判", "description": "配置审判参数并查看本地报告，群消息触发尚未接入",
    "default_enabled": False, "version": "0.1.0", "capability": "本地 HTML", "group": "群聊工具",
    "fields": [
        {"key": "trial_hours", "label": "审判范围（小时）", "type": "number", "max": 24},
        {"key": "trial_limit", "label": "消息条数上限", "type": "number", "max": 5000},
        {"key": "trial_cooldown", "label": "冷却时间（分钟）", "type": "number", "max": 60},
        {"key": "trial_intro", "label": "审判引导词", "type": "textarea", "max_length": 16000},
        {"key": "trial_summary", "label": "书记官汇总规则", "type": "textarea", "max_length": 16000},
        {"key": "trial_judges", "label": "法官模型 ID（每行一个，最多三个）", "type": "textarea", "max_length": 768},
        {"key": "trial_clerk", "label": "书记官模型 ID", "type": "input", "max_length": 256},
        {"key": "trial_prompt", "label": "审判规则", "type": "textarea", "max_length": 16000},
    ],
}
