"""Hetzner Cloud API service wrapper."""
from __future__ import annotations

from functools import lru_cache

from hcloud import Client
from hcloud.servers.domain import Server

from app.config import settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    if not settings.hetzner_api_key:
        raise RuntimeError("HETZNER_API_KEY is not configured")
    return Client(token=settings.hetzner_api_key)


# ── Servers ───────────────────────────────────────────────────────────────────

def list_servers() -> list[dict]:
    servers: list[Server] = get_client().servers.get_all()
    return [_server_to_dict(s) for s in servers]


def get_server(server_id: int) -> dict:
    server = get_client().servers.get_by_id(server_id)
    if server is None:
        raise KeyError(f"Server {server_id} not found")
    return _server_to_dict(server)


def power_on(server_id: int) -> dict:
    server = get_client().servers.get_by_id(server_id)
    if server is None:
        raise KeyError(f"Server {server_id} not found")
    action = server.power_on()
    return {"action_id": action.id, "status": action.status, "command": action.command}


def power_off(server_id: int) -> dict:
    server = get_client().servers.get_by_id(server_id)
    if server is None:
        raise KeyError(f"Server {server_id} not found")
    action = server.power_off()
    return {"action_id": action.id, "status": action.status, "command": action.command}


def reboot(server_id: int) -> dict:
    server = get_client().servers.get_by_id(server_id)
    if server is None:
        raise KeyError(f"Server {server_id} not found")
    action = server.reboot()
    return {"action_id": action.id, "status": action.status, "command": action.command}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _server_to_dict(s: Server) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "status": s.status,
        "server_type": s.server_type.name,
        "datacenter": s.datacenter.name,
        "location": s.datacenter.location.name,
        "ipv4": s.public_net.ipv4.ip if s.public_net.ipv4 else None,
        "ipv6": s.public_net.ipv6.ip if s.public_net.ipv6 else None,
        "created": s.created.isoformat() if s.created else None,
    }
