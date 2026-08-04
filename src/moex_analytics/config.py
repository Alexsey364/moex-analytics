"""Loading and validating project configuration."""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping and reject empty or non-mapping documents."""
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return value


def load_settings() -> dict[str, Any]:
    """Return application settings."""
    return load_yaml(CONFIG_DIR / "settings.yaml")


def load_instruments() -> list[dict[str, Any]]:
    """Return enabled instruments."""
    items = load_yaml(CONFIG_DIR / "instruments.yaml").get("instruments")
    if not isinstance(items, list):
        raise ValueError("'instruments' must be a list")
    return [item for item in items if item.get("is_active", False)]
