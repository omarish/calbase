from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = ".calbase"
DEFAULT_CONFIG_FILENAME = "config.json"
DEFAULT_TOKEN_FILENAME = "token.json"
DEFAULT_CREDENTIALS_FILENAME = "credentials.json"


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path
    config_path: Path
    token_path: Path
    credentials_path: Path


def get_app_paths() -> AppPaths:
    env_home = os.environ.get("CALBASE_HOME")
    base_dir = Path(env_home).expanduser() if env_home else Path.home() / APP_DIR_NAME
    return AppPaths(
        base_dir=base_dir,
        config_path=base_dir / DEFAULT_CONFIG_FILENAME,
        token_path=base_dir / DEFAULT_TOKEN_FILENAME,
        credentials_path=base_dir / DEFAULT_CREDENTIALS_FILENAME,
    )


def ensure_app_dir(paths: AppPaths) -> None:
    paths.base_dir.mkdir(parents=True, exist_ok=True)


def read_config(paths: AppPaths) -> dict:
    if not paths.config_path.exists():
        return {}
    return json.loads(paths.config_path.read_text(encoding="utf-8"))


def write_config(paths: AppPaths, config: dict) -> None:
    ensure_app_dir(paths)
    paths.config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True),
        encoding="utf-8",
    )
