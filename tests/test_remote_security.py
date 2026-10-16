import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler, build_opener
from urllib.response import addinfourl

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/chipseeker-ultra-search/scripts/chipseeker_remote.py"
SPEC = importlib.util.spec_from_file_location("remote_security_test", SCRIPT)
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)

BASE = "https://api.example.test/gateway"
SAVED_KEY = "synthetic-saved-key"
ENV_KEY = "synthetic-env-key"
EXPLICIT_KEY = "synthetic-explicit-key"


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch, tmp_path):
    monkeypatch.delenv("CHIPSEEKER_BASE_URL", raising=False)
    monkeypatch.delenv("CHIPSEEKER_API_KEY", raising=False)
    monkeypatch.setattr(remote, "CONFIG_PATH", tmp_path / "config.json")

    def read_config(path, default=None):
        assert path == remote.CONFIG_PATH
        return {"base_url": BASE, "api_key": SAVED_KEY, "email": "test@example.test"}

    def no_network(*args, **kwargs):
        pytest.fail("Unexpected network access")

    monkeypatch.setattr(remote, "_read_json", read_config)
    monkeypatch.setattr(remote, "build_opener", no_network)


@pytest.fixture
def transport(monkeypatch):
    calls = []
    response = {"quota": {"credits_left": 7}, "email": "test@example.test"}

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return io.BytesIO(json.dumps(response).encode())

    def factory(*handlers):
        assert len(handlers) == 1
        assert isinstance(handlers[0], remote._RejectRedirects)
        return Opener()

    monkeypatch.setattr(remote, "build_opener", factory)
    return calls, response


@pytest.mark.parametrize("command", ["auth-send", "auth-verify"])
@pytest.mark.parametrize("base", [BASE, "https://other.example.test/prefix"])
def test_auth_commands_never_send_existing_credentials(command, base, transport, monkeypatch, capsys):
    calls, response = transport
    if command == "auth-verify":
        response["api_key"] = "synthetic-new-login-key"
    monkeypatch.setenv("CHIPSEEKER_API_KEY", ENV_KEY)
    saved = []
    monkeypatch.setattr(remote, "save_config", lambda *args: saved.append(args))
    argv = [command, "--base-url", base, "--email", "test@example.test"]
    if command == "auth-verify":
        argv += ["--code", "123456"]
    args = remote.build_parser().parse_args(argv)
    args.handler(args)
    request, _ = calls[0]
    assert request.get_header("Authorization") is None
    assert request.full_url.startswith(base + "/api/v1/auth/")
    output = capsys.readouterr()
    for key in (SAVED_KEY, ENV_KEY, "synthetic-new-login-key"):
        assert key not in output.out + output.err
    if command == "auth-verify":
        assert saved == [(base, response["api_key"], response["email"])]


@pytest.mark.parametrize("path", ["/api/v1/auth", "/api/v1/auth/", "/api/v1/auth/send-code", "/api/v1/auth/verify", "/api/v1/auth/verify/?mode=test"])
def test_auth_api_ignores_even_explicit_key_without_loading_config(path, transport, monkeypatch):
    monkeypatch.setattr(remote, "load_config", lambda: pytest.fail("Auth does not need saved credentials"))
    remote.api_request("POST", path, base_url=BASE, api_key=EXPLICIT_KEY, payload={"code": "123456"})
    request, _ = transport[0][0]
    assert request.get_header("Authorization") is None


def test_auth_uses_configured_base_without_attaching_key(transport):
    remote.api_request("POST", "/api/v1/auth/verify", payload={"code": "123456"})
    request, _ = transport[0][0]
    assert request.full_url == BASE + "/api/v1/auth/verify"
    assert request.get_header("Authorization") is None


def test_requests_without_any_key_still_work(monkeypatch, transport):
    monkeypatch.setattr(remote, "_read_json", lambda *args: {})
    remote.api_request("GET", "/api/v1/quota")
    request, _ = transport[0][0]
    assert request.full_url == remote.DEFAULT_BASE_URL + "/api/v1/quota"
    assert request.get_header("Authorization") is None


@pytest.mark.parametrize("base", [BASE, "https://API.EXAMPLE.TEST:443/other-path"])
@pytest.mark.parametrize("source", ["saved", "env"])
def test_credentials_are_sent_only_to_same_origin(base, source, transport, monkeypatch):
    if source == "env":
        monkeypatch.setenv("CHIPSEEKER_API_KEY", ENV_KEY)
    remote.api_request("GET", "/api/v1/quota", base_url=base, timeout=19)
    request, timeout = transport[0][0]
    assert request.full_url == base + "/api/v1/quota"
    assert request.get_header("Authorization") == "Bearer " + (ENV_KEY if source == "env" else SAVED_KEY)
    assert timeout == 19


@pytest.mark.parametrize("base", ["https://other.example.test", "https://api.example.test:444", "http://api.example.test"])
@pytest.mark.parametrize("source", ["saved", "env"])
def test_inherited_key_cannot_be_sent_to_another_origin(base, source, monkeypatch):
    if source == "env":
        monkeypatch.setenv("CHIPSEEKER_API_KEY", ENV_KEY)
    with pytest.raises(ValueError):
        remote.api_request("GET", "/api/v1/quota", base_url=base)


def test_environment_base_override_does_not_rebind_saved_key(monkeypatch):
    monkeypatch.setenv("CHIPSEEKER_BASE_URL", "https://other.example.test")
    with pytest.raises(ValueError, match="different origin"):
        remote.api_request("GET", "/api/v1/quota")


def test_environment_key_and_base_are_bound_together(monkeypatch, transport):
    base = "https://env.example.test/prefix"
    monkeypatch.setenv("CHIPSEEKER_BASE_URL", base)
    monkeypatch.setenv("CHIPSEEKER_API_KEY", ENV_KEY)
    remote.api_request("GET", "/api/v1/quota")
    request, _ = transport[0][0]
    assert request.full_url == base + "/api/v1/quota"
    assert request.get_header("Authorization") == "Bearer " + ENV_KEY
    with pytest.raises(ValueError, match="different origin"):
        remote.api_request("GET", "/api/v1/quota", base_url=BASE)
    assert len(transport[0]) == 1


def test_explicit_key_can_target_requested_https_origin_without_config(monkeypatch, transport):
    monkeypatch.setattr(remote, "load_config", lambda: pytest.fail("Explicit configure must not read saved credentials"))
    remote.api_request("GET", "/api/v1/quota", base_url="https://new.example.test/prefix", api_key=EXPLICIT_KEY)
    request, _ = transport[0][0]
    assert request.full_url == "https://new.example.test/prefix/api/v1/quota"
    assert request.get_header("Authorization") == "Bearer " + EXPLICIT_KEY


@pytest.mark.parametrize("base", [
    "http://api.example.test", "http://localhost.example.test", "http://127.0.0.1.example.test",
    "http://192.168.1.1", "http://0.0.0.0", "http://[::]", "ftp://localhost", "file:///tmp/api",
    "https://user:password@api.example.test", "https://api.example.test:bad", "https://[invalid",
    "https://api.example.test/path?next=", "https://api.example.test/#fragment",
    "https://api.example.test/\npath", "https://api.example.test\\@evil.test",
])
@pytest.mark.parametrize("path", ["/api/v1/quota", "/api/v1/auth/verify"])
def test_unsafe_destinations_rejected_before_transport(base, path):
    with pytest.raises(ValueError):
        remote.api_request("POST", path, base_url=base, api_key=EXPLICIT_KEY, payload={"code": "123456"})


@pytest.mark.parametrize("base", ["http://localhost:8123/prefix", "http://127.0.0.1:8123", "http://127.0.0.2:8123", "http://[::1]:8123"])
def test_explicit_loopback_http_is_allowed(base, transport):
    remote.api_request("GET", "/api/v1/quota", base_url=base, api_key=EXPLICIT_KEY)
    request, _ = transport[0][0]
    assert request.get_header("Authorization") == "Bearer " + EXPLICIT_KEY
    assert request.full_url == base + "/api/v1/quota"


def test_saved_loopback_key_remains_origin_bound(monkeypatch, transport):
    base = "http://127.0.0.1:8123"
    monkeypatch.setattr(remote, "_read_json", lambda *args: {"base_url": base, "api_key": SAVED_KEY})
    remote.api_request("GET", "/api/v1/quota")
    assert transport[0][0][0].get_header("Authorization") == "Bearer " + SAVED_KEY
    with pytest.raises(ValueError, match="different origin"):
        remote.api_request("GET", "/api/v1/quota", base_url="http://127.0.0.1:8124")
    assert len(transport[0]) == 1


@pytest.mark.parametrize("path", ["https://other.example.test/api", "//other.example.test/api", "/\\other.example.test", "/api/\nquota"])
def test_path_cannot_override_destination(path):
    with pytest.raises(ValueError):
        remote.api_request("GET", path, base_url=BASE, api_key=EXPLICIT_KEY)


@pytest.mark.parametrize("source", ["argument", "env"])
def test_configure_preserves_base_path_and_settings_without_echoing_key(source, monkeypatch, transport, capsys):
    base = "https://new.example.test/api-prefix"
    monkeypatch.setattr(remote, "_read_json", lambda *args: pytest.fail("Must not read real credentials"))
    argv = ["configure", "--base-url", base]
    if source == "argument":
        argv += ["--api-key", EXPLICIT_KEY]
    else:
        monkeypatch.setenv("CHIPSEEKER_API_KEY", EXPLICIT_KEY)
    args = remote.build_parser().parse_args(argv)
    args.handler(args)
    saved = json.loads(remote.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["base_url"] == base
    assert saved["api_key"] == EXPLICIT_KEY
    assert saved["email"] == "test@example.test"
    request, _ = transport[0][0]
    assert request.full_url == base + "/api/v1/quota"
    assert request.get_header("Authorization") == "Bearer " + EXPLICIT_KEY
    output = capsys.readouterr()
    assert EXPLICIT_KEY not in output.out + output.err


def test_save_config_rejects_insecure_destination():
    with pytest.raises(ValueError):
        remote.save_config("http://remote.example.test", EXPLICIT_KEY)
    assert not remote.CONFIG_PATH.exists()


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", [
    "https://evil.example.test/collect", "http://api.example.test/collect",
    "https://api.example.test:444/collect", "//evil.example.test/collect", "/same-origin",
])
@pytest.mark.parametrize("method,path", [("GET", "/api/v1/quota"), ("POST", "/api/v1/search"), ("POST", "/api/v1/auth/verify")])
def test_real_urllib_redirect_processing_never_makes_second_request(code, location, method, path, monkeypatch):
    calls, responses = [], []

    class Wire(HTTPHandler, HTTPSHandler):
        def https_open(self, request):
            calls.append(request)
            headers = Message()
            headers["Location"] = location
            response = addinfourl(io.BytesIO(b"redirect"), headers, request.full_url, code)
            response.msg = "Redirect"
            responses.append(response)
            return response

        http_open = https_open

    monkeypatch.setattr(remote, "build_opener", lambda *handlers: build_opener(ProxyHandler({}), Wire(), *handlers))
    with pytest.raises(remote.RemoteError, match="redirects are disabled") as caught:
        remote.api_request(method, path, base_url=BASE, api_key=EXPLICIT_KEY, payload={"code": "123456"} if method == "POST" else None)
    assert caught.value.status == code
    assert len(calls) == 1
    assert responses[0].closed
    assert calls[0].get_header("Authorization") == (None if "/auth/" in path else "Bearer " + EXPLICIT_KEY)
    assert EXPLICIT_KEY not in str(caught.value)


def test_existing_http_error_status_payload_and_retry_after_survive_security_change(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            headers = Message()
            headers["Retry-After"] = "7"
            raise HTTPError(request.full_url, 429, "Busy", headers, io.BytesIO(b'{"detail":"busy","status":"pending"}'))

    monkeypatch.setattr(remote, "build_opener", lambda *handlers: Opener())
    with pytest.raises(remote.RemoteError) as caught:
        remote.api_request("GET", "/api/v1/quota")
    assert caught.value.status == 429
    assert caught.value.retry_after == 7
    assert caught.value.payload == {"detail": "busy", "status": "pending"}
