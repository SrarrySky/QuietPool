"""Forbid all network access during tests, including HTTP transports."""

import socket

import httpx
import pytest

import experiments  # noqa: F401 - bootstrap frozen vendor namespace


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in experiment tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
