"""DashScope-compatible OpenAI client for explanation generation."""

from openai import OpenAI

from app.config import config


model = config.dashscope_model


def get_client() -> OpenAI:
    """Create an OpenAI client after validating DashScope credentials."""

    return OpenAI(
        api_key=config.require_dashscope_api_key(),
        base_url=config.dashscope_base_url,
    )
