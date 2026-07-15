"""Local-services supervisor endpoints.

- GET  /                — status of every registered local daemon
- POST /{service_id}/restart — kill (if ours) and respawn, or adopt if it
                                 came back healthy on its own

See cowork/services/local_services.py for the registry itself.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from cowork.services.local_services import ServiceStatus, get_registry

router = APIRouter()


class ServiceStatusResponse(BaseModel):
    id: str
    label: str
    status: str
    detail: str = ""
    pid: int | None = None
    adopted: bool = False

    @classmethod
    def from_status(cls, s: ServiceStatus) -> "ServiceStatusResponse":
        return cls(id=s.id, label=s.label, status=s.status, detail=s.detail, pid=s.pid, adopted=s.adopted)


@router.get("/", response_model=list[ServiceStatusResponse])
def list_services() -> list[ServiceStatusResponse]:
    return [ServiceStatusResponse.from_status(s) for s in get_registry().list_status()]


@router.post("/{service_id}/restart", response_model=ServiceStatusResponse)
def restart_service(service_id: str) -> ServiceStatusResponse:
    try:
        result = get_registry().restart(service_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown service '{service_id}'")
    return ServiceStatusResponse.from_status(result)
