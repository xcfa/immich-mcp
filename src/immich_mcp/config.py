from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (and `.env` if present)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    immich_url: str = Field(description="Base URL of Immich, e.g. http://immich-server:2283")
    immich_api_key: SecretStr
    immich_public_url: str | None = Field(
        default=None,
        description="Immich address used in photo links for the user; defaults to immich_url",
    )
    immich_timeout: float = 30.0

    mcp_auth_token: SecretStr = Field(description="Shared bearer token MCP clients must present")
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
