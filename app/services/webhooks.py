import hashlib
import hmac
import ipaddress
import json
import socket
import time
from urllib.parse import urlparse

import httpx

from app.config import settings


class WebhookRetryable(Exception):
    """Delivery failed in a way that may succeed later."""


class WebhookRejected(Exception):
    """The receiver refused it; retrying will not help."""


def validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("webhook_url must be an http(s) URL")
    if settings.webhook_allow_private:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except OSError:
        raise ValueError("webhook_url host could not be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("webhook_url must not point to a private address")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def build_request(event: str, delivery_id: str, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "docpipe-webhooks/1.0",
        "X-Docpipe-Event": event,
        "X-Docpipe-Delivery": delivery_id,
        "X-Docpipe-Timestamp": timestamp,
        "X-Docpipe-Signature": sign(settings.webhook_secret, timestamp, body),
    }
    return body, headers


def post_webhook(url: str, body: bytes, headers: dict) -> None:
    """Returns on 2xx. Raises WebhookRetryable or WebhookRejected otherwise."""
    try:
        r = httpx.post(url, content=body, headers=headers, timeout=5.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise WebhookRetryable(f"{type(exc).__name__}") from exc
    if 200 <= r.status_code < 300:
        return
    if r.status_code in (408, 429) or r.status_code >= 500:
        raise WebhookRetryable(f"HTTP {r.status_code}")
    raise WebhookRejected(f"HTTP {r.status_code}")