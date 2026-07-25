import ipaddress
from urllib.parse import urlparse


OFFICIAL_WEBHOOK_HOSTS = {"open.feishu.cn", "open.larksuite.com"}
OFFICIAL_WEBHOOK_PATH = "/open-apis/bot/v2/hook/"


def is_allowed_webhook_url(
    webhook_url: object, *, allow_loopback_http: bool = False
) -> bool:
    if not isinstance(webhook_url, str):
        return False
    try:
        parsed = urlparse(webhook_url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False

    if parsed.scheme == "https":
        if (
            hostname.lower() not in OFFICIAL_WEBHOOK_HOSTS
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or parsed.params
            or not parsed.path.startswith(OFFICIAL_WEBHOOK_PATH)
        ):
            return False
        token = parsed.path[len(OFFICIAL_WEBHOOK_PATH) :]
        return bool(token) and "/" not in token

    if parsed.scheme != "http" or not allow_loopback_http:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
