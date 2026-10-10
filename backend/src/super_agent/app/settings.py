"""settings.json 读写（spec：审批白名单、默认模型、并发上限）。v1 只落审批白名单。"""

import json
from pathlib import Path

DEFAULT_FILE = "settings.json"


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_allowlist(self) -> set[str]:
        names = self.load().get("approval_allowlist")
        return {str(n) for n in names} if isinstance(names, list) else set()

    def save_allowlist(self, names: set[str]) -> None:
        data = self.load()
        data["approval_allowlist"] = sorted(names)
        self.save(data)

    def load_custom_models(self) -> list[dict]:
        models = self.load().get("custom_models")
        return [dict(m) for m in models if isinstance(m, dict)] if isinstance(models, list) else []

    def load_max_concurrency(self, fallback: int = 4) -> int:
        try:
            return max(1, int(self.load().get("max_concurrency") or fallback))
        except (TypeError, ValueError):
            return fallback

    def load_default_model(self) -> str | None:
        model = self.load().get("default_model")
        return str(model) if model else None
