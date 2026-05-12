"""Hetzner Cloud management routes."""
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.services import hetzner as hetzner_svc

router = APIRouter(prefix="/api/hetzner", tags=["hetzner"])


@router.get("/servers")
def list_servers(user: dict = Depends(get_current_user)) -> list[dict]:
    try:
        return hetzner_svc.list_servers()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/servers/{server_id}")
def get_server(server_id: int, user: dict = Depends(get_current_user)) -> dict:
    try:
        return hetzner_svc.get_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/servers/{server_id}/power-on")
def power_on(server_id: int, user: dict = Depends(get_current_user)) -> dict:
    try:
        return hetzner_svc.power_on(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/servers/{server_id}/power-off")
def power_off(server_id: int, user: dict = Depends(get_current_user)) -> dict:
    try:
        return hetzner_svc.power_off(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/servers/{server_id}/reboot")
def reboot(server_id: int, user: dict = Depends(get_current_user)) -> dict:
    try:
        return hetzner_svc.reboot(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
