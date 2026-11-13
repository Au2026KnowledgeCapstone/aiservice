"""Target-origin-only network policy. Every request is re-checked, not just the first.

No service_origins/write-allowlist/objective_checks here — those come from a
BrowserConfiguration the job envelope does not carry (see the A4 handoff notes).
Metadata, link-local and RFC1918-style private addresses are always blocked,
with no allowlist, since the engine has no configuration channel to populate
one. Loopback is the one carve-out: it is the engine's own container, not
another host on the network, and dev/test fixtures need it reachable.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from engines.mvp.errors import PolicyDenied

_METADATA_HOSTS = {"metadata.google.internal", "169.254.169.254"}
_METADATA_ADDRESSES = {"169.254.169.254", "169.254.170.2", "fd00:ec2::254"}


def origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Use an HTTP(S) origin without credentials or fragment")
    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    display = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{display}" + (f":{port}" if port != default_port else "")


def safe_url(url: str) -> str:
    try:
        return origin(url)
    except ValueError:
        return "[blocked URL]"


async def _resolve(hostname: str, port: int) -> list[str]:
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM), 5
        )
    except (OSError, TimeoutError):
        raise PolicyDenied("Hostname could not be resolved") from None
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise PolicyDenied("No destination addresses")
    return addresses


async def validate_address(url: str) -> None:
    """DNS-resolve the URL's host and refuse metadata, link-local or private addresses."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname in _METADATA_HOSTS:
        raise PolicyDenied("Metadata endpoint blocked")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for address in await _resolve(hostname, port):
        if address in _METADATA_ADDRESSES:
            raise PolicyDenied("Metadata endpoint blocked")
        ip = ipaddress.ip_address(address)
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (ip.is_reserved and not ip.is_loopback):
            raise PolicyDenied("Reserved or metadata destination blocked")
        # Loopback is the engine's own container, not another host on the network;
        # it is the one non-global category allowed without a configuration channel.
        if not ip.is_global and not ip.is_loopback:
            raise PolicyDenied("Private destination is blocked")


class NetworkPolicy:
    """Approves only the target's own origin. No write-allowlist, no extra origins."""

    def __init__(self, target_url: str):
        self.target_origin = origin(target_url)
        self.denials: list[dict] = []

    async def check(self, url: str, method: str = "GET", navigation: bool = False) -> str:
        try:
            resolved = origin(url)
        except ValueError:
            raise PolicyDenied("Unsupported browser URL") from None
        if resolved != self.target_origin:
            raise PolicyDenied("Origin not approved")
        if navigation and resolved != self.target_origin:
            raise PolicyDenied("Navigation outside the target origin is blocked")
        await validate_address(url)
        return resolved

    def denied(self, url: str, reason: str, critical: bool = False) -> None:
        event = {"origin": safe_url(url), "reason": str(reason), "critical": critical}
        if event not in self.denials and len(self.denials) < 30:
            self.denials.append(event)
