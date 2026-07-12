"""Tests for ``_is_safe_url`` — SSRF protection on the web_search tool.

These guard the DNS-resolving allow/deny logic that keeps the AI from
fetching internal services (localhost, RFC1918 ranges, cloud metadata).
``_is_safe_url`` imports ``socket`` at call time, so patching
``socket.getaddrinfo`` exercises the hostname-resolution path.
"""

import socket

import pytest

from polyglot_ai.core.ai.tools.shell_tools import _is_safe_url


def _addrinfo(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


class TestLiteralAndScheme:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/",
            "http://127.0.0.1/",
            "http://127.0.0.1:8080/admin",
            "https://[::1]/",
            "http://0.0.0.0/",
        ],
    )
    def test_rejects_loopback(self, url):
        assert _is_safe_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        ],
    )
    def test_rejects_private_ip_literals(self, url):
        assert _is_safe_url(url) is False

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "ftp://example.com/", "gopher://example.com/"],
    )
    def test_rejects_non_http_schemes(self, url):
        assert _is_safe_url(url) is False

    def test_rejects_empty_host(self):
        assert _is_safe_url("http://") is False

    def test_allows_public_ip_literal(self):
        assert _is_safe_url("http://8.8.8.8/") is True


class TestDnsResolution:
    def test_rejects_hostname_resolving_to_private(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("10.0.0.7"))
        assert _is_safe_url("http://evil.example.com/") is False

    def test_rejects_hostname_resolving_to_metadata(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("169.254.169.254"))
        assert _is_safe_url("http://metadata.evil/") is False

    def test_allows_hostname_resolving_to_public(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
        assert _is_safe_url("http://example.com/") is True

    def test_rejects_unresolvable_hostname(self, monkeypatch):
        def _boom(*a, **k):
            raise socket.gaierror("name resolution failed")

        monkeypatch.setattr(socket, "getaddrinfo", _boom)
        assert _is_safe_url("http://nonexistent.invalid/") is False

    def test_rejects_when_any_resolved_address_is_unsafe(self, monkeypatch):
        # DNS-rebinding shape: one public address plus a loopback one.
        # If *any* resolved address is unsafe the URL must be rejected.
        def _mixed(*a, **k):
            return _addrinfo("93.184.216.34") + _addrinfo("127.0.0.1")

        monkeypatch.setattr(socket, "getaddrinfo", _mixed)
        assert _is_safe_url("http://rebind.example/") is False
