"""A small, strict HTTP client for talking to local model servers.

* Loopback only unless the user opted in (`allow_remote`): the repository's code is sent
  to these servers, so a typo or a hostile config must not exfiltrate it.
* Never follows redirects (a loopback server cannot bounce us to a remote host).
* Ignores system proxy settings, so loopback traffic is never sent to a proxy.
* Bounded response size and timeouts; every failure becomes a ProviderError with a
  short kind the UI can explain.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 8_000_000


class ProviderError(Exception):
    """kind: blocked | unreachable | timeout | http | bad_response | unsupported"""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def check_endpoint_url(url: str, allow_remote: bool = False) -> str:
    """Validate and normalise a base URL. Raises ProviderError('blocked', ...)."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port  # raises ValueError on a bad port
    except ValueError as e:
        raise ProviderError("blocked", f"not a valid URL: {url!r}") from e
    if parts.scheme not in ("http", "https"):
        raise ProviderError("blocked", "endpoint must start with http:// or https://")
    if parts.username or parts.password:
        raise ProviderError("blocked", "put credentials in an environment variable (api_key_env), not in the URL")
    host = parts.hostname
    if not host:
        raise ProviderError("blocked", "endpoint has no host")
    if parts.query or parts.fragment:
        raise ProviderError("blocked", "endpoint must be a base URL without a query or fragment")
    if not allow_remote and not is_loopback_host(host):
        raise ProviderError(
            "blocked",
            f"{host} is not a loopback address. Your code would be sent to it. "
            "Use 127.0.0.1/localhost, or set allow_remote to true if you really mean it.")
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # any other hostname could resolve anywhere, so it is not accepted


def _map_error(e: Exception) -> ProviderError:
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = e.read(300).decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return ProviderError("http", f"HTTP {e.code}" + (f": {detail}" if detail else ""), e.code)
    reason = getattr(e, "reason", e)
    if isinstance(e, socket.timeout) or isinstance(reason, socket.timeout) or isinstance(e, TimeoutError):
        return ProviderError("timeout", "the server did not answer in time")
    if isinstance(e, (urllib.error.URLError, ConnectionError, OSError)):
        return ProviderError("unreachable", f"cannot connect ({reason})")
    return ProviderError("bad_response", str(e))


def open_request(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None,
                 timeout: float = 3.0):
    """Low-level: returns the open response (caller closes it). Used for streaming too."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        return _OPENER.open(req, timeout=timeout)
    except Exception as e:
        raise _map_error(e) from e


def request_json(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None,
                 timeout: float = 3.0):
    resp = open_request(url, method, body, headers, timeout)
    try:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except Exception as e:
        raise _map_error(e) from e
    finally:
        resp.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("bad_response", "response too large")
    try:
        return json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError as e:
        raise ProviderError("bad_response", "the server did not return JSON") from e
