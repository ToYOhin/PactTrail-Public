"""Fixed display candidates; selecting one never connects to a model service."""

_CATALOG = (
    ("offline", "离线演示", "demo", (), ""),
    (
        "openai", "OpenAI", "international", (),
        "https://platform.openai.com/docs/guides/text",
    ),
    (
        "anthropic", "Claude", "international", (),
        "https://platform.claude.com/docs/en/api/http/messages/create",
    ),
    (
        "gemini", "Gemini", "international", (),
        "https://ai.google.dev/gemini-api/docs/openai",
    ),
    (
        "deepseek", "DeepSeek", "domestic",
        ("deepseek-flash", "deepseek-v4-pro"),
        "https://api-docs.deepseek.com/",
    ),
    (
        "qwen", "通义千问", "domestic", ("qwen3.8-max",),
        "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions",
    ),
    (
        "kimi", "Kimi", "domestic", ("kimi-k3", "kimi-k2.6"),
        "https://platform.kimi.com/docs/get-api-key",
    ),
    (
        "glm", "GLM", "domestic", ("glm-5.2",),
        "https://docs.bigmodel.cn/cn/guide/develop/openai/introduction",
    ),
)


def model_catalog() -> list[dict[str, str | list[str]]]:
    """Return detached entries, including detached model-example lists."""
    return [
        {
            "id": provider_id,
            "label": label,
            "group": group,
            "availability": "offline_demo" if provider_id == "offline" else "not_configured",
            "model_examples": list(examples),
            "documentation_url": documentation_url,
        }
        for provider_id, label, group, examples, documentation_url in _CATALOG
    ]
