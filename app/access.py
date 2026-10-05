"""Tell LAN requests from internet requests (tunnels such as ngrok / Cloudflare)."""

import ipaddress
import socket

from . import config

_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale and carrier NAT
_HOSTNAME = socket.gethostname().lower()
LOCAL_NAMES = {"localhost", _HOSTNAME, f"{_HOSTNAME}.local", *(h.lower() for h in config.ALLOWED_HOSTS)}


def is_local_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or (ip.version == 4 and ip in _CGNAT)


def _host_only(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_remote(scope: dict) -> bool:
    """True when the request did not come straight from the local network.

    A request counts as remote if the peer is public, if any proxy hop in X-Forwarded-For is public
    (ngrok, cloudflared), or if the Host header is not a LAN name — which also defeats DNS rebinding.
    """
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    client = scope.get("client")
    if client and not is_local_ip(client[0]):
        return True
    for hop in headers.get("x-forwarded-for", "").split(","):
        if hop.strip() and not is_local_ip(hop):
            return True
    host = _host_only(headers.get("host", ""))
    return bool(host) and not (is_local_ip(host) or host in LOCAL_NAMES)
