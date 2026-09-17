"""Keep correctness tests independent of local credentials and live services."""

import socket

import pytest

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(scope="session", autouse=True)
def offline_environment():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("INTELLIHUB_ENV_FILE", "/dev/null")
    for name in ("INTELLIHUB_API_KEY", "INTELLIHUB_BASE_URL", "INTELLIHUB_MODEL", "INTELLIHUB_MODEL_ID", "api_key"):
        monkeypatch.setenv(name, "")

    real_connect, real_create_connection = socket.socket.connect, socket.create_connection

    def loopback_only(address) -> None:
        # Local test doubles (for example scripts/fake_gpt_oss_gateway.py) listen on 127.0.0.1;
        # every other destination, including a configured live gateway, stays blocked.
        if not (isinstance(address, tuple) and address[0] in LOOPBACK_HOSTS):
            raise AssertionError("Tests must mock transport; live network access is disabled")

    def connect(self, address):
        loopback_only(address)
        return real_connect(self, address)

    def create_connection(address, *args, **kwargs):
        loopback_only(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield
    monkeypatch.undo()
