"""Application configuration loaded from environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for DashScope LLM calls and ranking defaults."""

    dashscope_api_key: str | None = Field(None, alias="DASHSCOPE_API_KEY")
    dashscope_base_url: str = Field(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="DASHSCOPE_BASE_URL",
    )
    dashscope_model: str = Field("qwen-plus", alias="DASHSCOPE_MODEL")
    current_year: int = Field(2026, alias="CURRENT_YEAR")
    province_total_rank: int = Field(320000, alias="PROVINCE_TOTAL_RANK")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def DASHSCOPE_API_KEY(self) -> str | None:
        """Compatibility accessor matching the environment variable name."""

        return self.dashscope_api_key

    @property
    def DASHSCOPE_BASE_URL(self) -> str:
        """Compatibility accessor matching the environment variable name."""

        return self.dashscope_base_url

    @property
    def DASHSCOPE_MODEL(self) -> str:
        """Compatibility accessor matching the environment variable name."""

        return self.dashscope_model

    def require_dashscope_api_key(self) -> str:
        """Return the DashScope API key or raise when an LLM call is requested."""

        if not self.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for LLM calls.")
        return self.dashscope_api_key


config = Settings()

