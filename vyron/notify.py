"""Push a notice to the user's phone via ntfy (https://ntfy.sh): free, no account, an app on the phone."""

from __future__ import annotations

from .config import secret


class NtfyNotifier:
    def __init__(self, topic: str, server: str = "https://ntfy.sh", transport=None):
        import httpx
        self.topic = topic.strip().strip("/")
        self.url = f"{server.rstrip('/')}/{self.topic}"
        self._client = httpx.Client(timeout=10, transport=transport)

    def send(self, title: str, text: str, priority: str = "default") -> bool:
        try:
            r = self._client.post(self.url, content=text.encode("utf-8"),
                                  headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": priority})
            return r.status_code < 400
        except Exception:  # noqa: BLE001
            return False


def notifier_from_env(config) -> NtfyNotifier | None:
    topic = secret("NTFY_TOPIC") or config.get("notify", "ntfy_topic", "")
    return NtfyNotifier(topic, config.get("notify", "ntfy_server", "https://ntfy.sh")) if topic else None
