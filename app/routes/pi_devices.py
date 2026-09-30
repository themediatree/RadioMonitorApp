"""
Pi Device management routes (internal admin only).

    GET  /admin/pi-devices              device list + live status
    GET  /admin/pi-devices/new          add device form
    POST /admin/pi-devices              create device
    GET  /admin/pi-devices/{id}         device detail / status
    GET  /admin/pi-devices/{id}/edit    edit form
    POST /admin/pi-devices/{id}         update device
    POST /admin/pi-devices/{id}/toggle  activate / deactivate
    POST /admin/pi-devices/{id}/rotate-key  regenerate API key
"""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_internal
from app.models.pi_device import PiDevice
from app.models.station import Station
from app.models.user import User
from app.templating import templates

router = APIRouter(tags=["admin-pi"], dependencies=[Depends(require_internal)])


def _stations(db: Session) -> list[Station]:
    return db.query(Station).order_by(Station.StationName).all()


# ── List ──────────────────────────────────────────────────────

@router.get("/admin/pi-devices", response_class=HTMLResponse)
def pi_device_list(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    devices = (
        db.query(PiDevice)
        .outerjoin(Station, Station.StationID == PiDevice.StationID)
        .order_by(PiDevice.DeviceName)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/pi_devices.html",
        context={"user": user, "devices": devices},
    )


# ── Create ────────────────────────────────────────────────────

@router.get("/admin/pi-devices/new", response_class=HTMLResponse)
def pi_device_new_form(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    return templates.TemplateResponse(
        request=request,
        name="admin/pi_device_form.html",
        context={
            "user": user, "device": None,
            "stations": _stations(db),
            "error": None, "form": {},
        },
    )


@router.post("/admin/pi-devices")
def pi_device_create(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    device_name:  Annotated[str, Form()],
    vpn_ip:       Annotated[str, Form()],
    station_id:   Annotated[str, Form()] = "",
    frequency:    Annotated[str, Form()] = "",
    stream_url:   Annotated[str, Form()] = "",
    power_source: Annotated[str, Form()] = "",
):
    def rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/pi_device_form.html",
            context={
                "user": user, "device": None,
                "stations": _stations(db),
                "error": error,
                "form": {
                    "device_name": device_name, "vpn_ip": vpn_ip,
                    "station_id": station_id, "frequency": frequency,
                    "stream_url": stream_url, "power_source": power_source,
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not device_name.strip():
        return rerender("Device name is required.")
    if not vpn_ip.strip():
        return rerender("VPN IP is required.")

    # Check uniqueness
    if db.query(PiDevice).filter(PiDevice.DeviceName == device_name.strip()).first():
        return rerender(f"Device name '{device_name}' already exists.")
    if db.query(PiDevice).filter(PiDevice.VpnIP == vpn_ip.strip()).first():
        return rerender(f"VPN IP '{vpn_ip}' is already assigned to another device.")

    sid = int(station_id) if station_id.isdigit() else None
    freq = float(frequency) if frequency.replace(".", "").isdigit() else None

    device = PiDevice(
        DeviceName=device_name.strip(),
        VpnIP=vpn_ip.strip(),
        StationID=sid,
        ApiKey=str(uuid.uuid4()),
        IsActive=True,
        Frequency=freq,
        StreamUrl=stream_url.strip() or None,
        PowerSource=power_source.strip() or None,
    )
    db.add(device)
    db.commit()
    return RedirectResponse(
        f"/admin/pi-devices/{device.PiDeviceID}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Detail ────────────────────────────────────────────────────

@router.get("/admin/pi-devices/{device_id}", response_class=HTMLResponse)
def pi_device_detail(
    device_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    device = db.get(PiDevice, device_id)
    if not device:
        return RedirectResponse("/admin/pi-devices", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="admin/pi_device_detail.html",
        context={"user": user, "device": device},
    )


# ── Edit ──────────────────────────────────────────────────────

@router.get("/admin/pi-devices/{device_id}/edit", response_class=HTMLResponse)
def pi_device_edit_form(
    device_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    device = db.get(PiDevice, device_id)
    if not device:
        return RedirectResponse("/admin/pi-devices", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="admin/pi_device_form.html",
        context={
            "user": user, "device": device,
            "stations": _stations(db),
            "error": None, "form": {},
        },
    )


@router.post("/admin/pi-devices/{device_id}")
def pi_device_update(
    device_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    device_name:  Annotated[str, Form()],
    vpn_ip:       Annotated[str, Form()],
    station_id:   Annotated[str, Form()] = "",
    frequency:    Annotated[str, Form()] = "",
    stream_url:   Annotated[str, Form()] = "",
    power_source: Annotated[str, Form()] = "",
):
    device = db.get(PiDevice, device_id)
    if not device:
        return RedirectResponse("/admin/pi-devices", status_code=status.HTTP_303_SEE_OTHER)

    def rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/pi_device_form.html",
            context={
                "user": user, "device": device,
                "stations": _stations(db),
                "error": error, "form": {},
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not device_name.strip():
        return rerender("Device name is required.")
    if not vpn_ip.strip():
        return rerender("VPN IP is required.")

    # Uniqueness checks (excluding self)
    clash_name = db.query(PiDevice).filter(
        PiDevice.DeviceName == device_name.strip(),
        PiDevice.PiDeviceID != device_id,
    ).first()
    if clash_name:
        return rerender(f"Device name '{device_name}' already exists.")

    clash_ip = db.query(PiDevice).filter(
        PiDevice.VpnIP == vpn_ip.strip(),
        PiDevice.PiDeviceID != device_id,
    ).first()
    if clash_ip:
        return rerender(f"VPN IP '{vpn_ip}' is already assigned to another device.")

    sid  = int(station_id) if station_id.isdigit() else None
    freq = float(frequency) if frequency.replace(".", "").isdigit() else None

    device.DeviceName  = device_name.strip()
    device.VpnIP       = vpn_ip.strip()
    device.StationID   = sid
    device.Frequency   = freq
    device.StreamUrl   = stream_url.strip() or None
    device.PowerSource = power_source.strip() or None
    db.commit()
    return RedirectResponse(
        f"/admin/pi-devices/{device_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Toggle active ─────────────────────────────────────────────

@router.post("/admin/pi-devices/{device_id}/toggle")
def pi_device_toggle(
    device_id: int,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    device = db.get(PiDevice, device_id)
    if device:
        device.IsActive = not device.IsActive
        db.commit()
    return RedirectResponse(
        f"/admin/pi-devices/{device_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Rotate API key ────────────────────────────────────────────

@router.post("/admin/pi-devices/{device_id}/rotate-key")
def pi_device_rotate_key(
    device_id: int,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    device = db.get(PiDevice, device_id)
    if device:
        device.ApiKey = str(uuid.uuid4())
        db.commit()
    return RedirectResponse(
        f"/admin/pi-devices/{device_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Live status JSON (for dashboard auto-refresh) ─────────────

@router.get("/api/admin/pi-devices/status")
def pi_device_status_json(
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    """Returns live status for all devices — polled by the admin dashboard."""
    devices = db.query(PiDevice).order_by(PiDevice.DeviceName).all()
    return JSONResponse([
        {
            "id":               d.PiDeviceID,
            "name":             d.DeviceName,
            "vpn_ip":           d.VpnIP,
            "station":          d.station.StationName if d.station else None,
            "is_active":        d.IsActive,
            "status_label":     d.status_label,
            "status_colour":    d.status_colour,
            "last_seen_ago":    d.last_seen_ago,
            "internet_source":  d.InternetSource,
            "internet_up":      d.InternetConnected,
            "fm_signal":        d.FmSignal,
            "stream_active":    d.StreamActive,
            "frequency":        float(d.Frequency) if d.Frequency else None,
            "power_source":     d.PowerSource,
            "uptime":           d.Uptime,
        }
        for d in devices
    ])
