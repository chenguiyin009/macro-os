"""Macro OS - pydantic-settings configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

from core.config_validation import load_yaml


class ThresholdConfig(BaseModel):
    """Typed container for threshold values from YAML."""

    regime: Dict[str, Any] = Field(default_factory=dict)
    scoring: Dict[str, Any] = Field(default_factory=dict)
    decision: Dict[str, Any] = Field(default_factory=dict)
    constitution: Dict[str, Any] = Field(default_factory=dict)
    system: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> "ThresholdConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)


class Settings(BaseSettings):
    """Application settings loaded from environment + YAML.

    Environment variables take precedence over YAML values.
    Uses pydantic-settings for env var resolution.
    """

    model_config = SettingsConfigDict(
        env_prefix="MACRO_OS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )
    vault_dir: Path = Field(default=Path("vault"))
    config_dir: Path = Field(default=Path("config"))

    mcp_timeout_seconds: int = 8
    scheduler_interval_minutes: int = 15
    max_retries: int = 3

    feishu_webhook_url: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None

    mcp_command: str = "node"
    mcp_script_path: str = ""
    # Prefer FRED CSV live macro when TV MCP/relay unavailable.
    fred_enabled: bool = True
    hydrate_session_from_vault: bool = True

    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    watchlist: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Load threshold and watchlist YAML on init."""
        thresholds_path = self.project_root / self.config_dir / "thresholds.yaml"
        if thresholds_path.exists():
            self.thresholds = ThresholdConfig.from_yaml(thresholds_path)

        watchlist_path = self.project_root / self.config_dir / "watchlist.yaml"
        if watchlist_path.exists():
            self.watchlist = load_yaml(watchlist_path)

    @property
    def vault_path(self) -> Path:
        return self.project_root / self.vault_dir

    @property
    def events_path(self) -> Path:
        return self.vault_path / "EVENTS.log.jsonl"

    @property
    def config_path(self) -> Path:
        return self.project_root / self.config_dir


settings = Settings()
