"""Towers map — ICASA TBFP FM broadcast footprint map."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.tbfp_station import TBFPStation, CATEGORY_COLOURS
from app.models.user import User
from app.templating import templates

router = APIRouter(tags=["towers"])


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@router.get("/towers", response_class=HTMLResponse)
def towers_map(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    total = db.query(TBFPStation).count()
    total_programs = db.query(TBFPStation.Station_name).filter(
        TBFPStation.Station_name.isnot(None)
    ).distinct().count()
    by_cat = {
        cat: db.query(TBFPStation.Station_name).filter(
            TBFPStation.Category == cat,
            TBFPStation.Station_name.isnot(None),
        ).distinct().count()
        for cat in ["PBS", "CML", "CTY", "LP"]
    }
    return templates.TemplateResponse(
        request=request,
        name="app/towers.html",
        context={
            "user": user,
            "total_stations": total,
            "total_programs": total_programs,
            "by_category": by_cat,
            **right_panel_context(user, db, request),
        },
    )


# ---------------------------------------------------------------------------
# API: Station list (unique Station_name, alphabetical)
# ---------------------------------------------------------------------------

@router.get("/api/map/stations")
def api_map_stations(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    q:     Optional[str] = Query(default=None),
    cat:   Optional[str] = Query(default=None),
    limit: int           = Query(default=300, le=500),
):
    query = db.query(
        TBFPStation.Station_name,
        TBFPStation.Category,
        TBFPStation.LogoPath,
        func.count(TBFPStation.TBFPStationID).label("tower_count"),
        func.min(TBFPStation.FreqMHz).label("freq"),
        func.max(TBFPStation.StreamURL).label("stream_url"),
    ).filter(TBFPStation.Station_name.isnot(None))

    if cat:
        query = query.filter(TBFPStation.Category == cat.upper())

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                TBFPStation.Station_name.ilike(term),
                TBFPStation.Tower_location.ilike(term),
            )
        )

    results = (
        query.group_by(
            TBFPStation.Station_name,
            TBFPStation.Category,
            TBFPStation.LogoPath,
        )
        .order_by(TBFPStation.Station_name)
        .limit(limit)
        .all()
    )

    # Sort: stations with stream URL first, no-audio stations at bottom
    results = sorted(results, key=lambda r: (0 if r.stream_url else 1, (r.Station_name or '').lower()))

    return JSONResponse([
        {
            "program":      r.Station_name,
            "category":     r.Category or "",
            "tower_count":  r.tower_count,
            "freq":         r.freq,
            "color":        CATEGORY_COLOURS.get(r.Category or "", "#888888"),
            "logo_url":     f"/static/station_logos/{r.LogoPath}" if r.LogoPath else None,
            "has_stream":   bool(r.stream_url),
        }
        for r in results
    ])


# ---------------------------------------------------------------------------
# API: Single station GeoJSON
# ---------------------------------------------------------------------------

@router.get("/api/map/station/{station_id}")
def api_map_station(
    station_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    station = db.get(TBFPStation, station_id)
    if not station:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(station.to_geojson())


# ---------------------------------------------------------------------------
# API: All tower locations for a given Station_name (program)
# ---------------------------------------------------------------------------

@router.get("/api/map/program")
def api_map_program(
    program: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    stations = (
        db.query(TBFPStation)
        .filter(TBFPStation.Station_name == program)
        .order_by(TBFPStation.Tower_location)
        .all()
    )
    if not stations:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse([s.to_geojson() for s in stations])


# ---------------------------------------------------------------------------
# API: Tower list (unique Tower_location sites)
# ---------------------------------------------------------------------------

@router.get("/api/map/towers")
def api_map_towers(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    q:     Optional[str] = Query(default=None),
    cat:   Optional[str] = Query(default=None),
    limit: int           = Query(default=300, le=500),
):
    query = db.query(
        TBFPStation.Tower_location,
        TBFPStation.LatDecimal,
        TBFPStation.LonDecimal,
        func.count(TBFPStation.TBFPStationID).label("station_count"),
    )

    if cat:
        query = query.filter(TBFPStation.Category == cat.upper())

    if q:
        query = query.filter(TBFPStation.Tower_location.ilike(f"%{q}%"))

    results = (
        query.group_by(
            TBFPStation.Tower_location,
            TBFPStation.LatDecimal,
            TBFPStation.LonDecimal,
        )
        .order_by(TBFPStation.Tower_location)
        .limit(limit)
        .all()
    )

    return JSONResponse([
        {
            "name":          r.Tower_location,
            "lat":           r.LatDecimal,
            "lon":           r.LonDecimal,
            "station_count": r.station_count,
            "color":         "#4a8eff",
        }
        for r in results
    ])


# ---------------------------------------------------------------------------
# API: All stations broadcasting from a named tower location
# ---------------------------------------------------------------------------

@router.get("/api/map/tower-stations")
def api_map_tower_stations(
    tower_name: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    stations = (
        db.query(TBFPStation)
        .filter(TBFPStation.Tower_location == tower_name)
        .order_by(TBFPStation.Station_name)
        .all()
    )
    if not stations:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse([s.to_geojson() for s in stations])


# ---------------------------------------------------------------------------
# API: Get single station for edit form
# ---------------------------------------------------------------------------

@router.get("/api/map/station/{station_id}/edit")
def api_map_station_edit(
    station_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Return full editable fields for a single TBFPStation row."""
    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    station = db.get(TBFPStation, station_id)
    if not station:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({
        "id":           station.TBFPStationID,
        "station_name": station.Station_name or "",
        "tower":        station.Tower_location or "",
        "freq_mhz":     station.FreqMHz,
        "erp_kw":       station.ErpKw,
        "category":     station.Category or "",
        "province":     station.Province or "",
        "logo_path":    station.LogoPath or "",
        "stream_url":   station.StreamURL or "",
        "unit_ip":      station.UnitIP or "",
    })


# ---------------------------------------------------------------------------
# API: PATCH single station record
# ---------------------------------------------------------------------------

@router.patch("/api/map/station/{station_id}")
async def api_map_station_patch(
    station_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Update editable fields on a single TBFPStation row."""
    import math
    from app.models.user import UserType

    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    station = db.get(TBFPStation, station_id)
    if not station:
        return JSONResponse({"error": "Not found"}, status_code=404)

    body = await request.json()

    # Apply editable fields
    if "station_name" in body:
        station.Station_name = body["station_name"] or None
    if "freq_mhz" in body:
        station.FreqMHz = float(body["freq_mhz"])
    if "erp_kw" in body:
        erp = float(body["erp_kw"])
        station.ErpKw = erp
        # Recalculate RadiusKm
        station.RadiusKm = round(min(28 * math.sqrt(erp), 120), 2) if erp > 0 else 0.0
    if "category" in body:
        station.Category = body["category"] or None
    if "province" in body:
        station.Province = body["province"] or None
    if "logo_path" in body:
        station.LogoPath = body["logo_path"] or None
    if "stream_url" in body:
        station.StreamURL = body["stream_url"] or None
    if "unit_ip" in body:
        station.UnitIP = body["unit_ip"] or None

    db.commit()
    return JSONResponse(station.to_geojson())


# ---------------------------------------------------------------------------
# API: Update ALL tower rows for a Station_name (shared station fields)
# ---------------------------------------------------------------------------

@router.patch("/api/map/program-update")
async def api_map_program_update(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Update shared fields across ALL towers for a given Station_name."""
    import math as _math
    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    body = await request.json()
    program = body.get("station_name_original")  # current name to match rows
    if not program:
        return JSONResponse({"error": "station_name_original required"}, status_code=400)

    rows = (
        db.query(TBFPStation)
        .filter(TBFPStation.Station_name == program)
        .all()
    )
    if not rows:
        return JSONResponse({"error": "No rows found"}, status_code=404)

    for row in rows:
        if "station_name" in body and body["station_name"]:
            row.Station_name = body["station_name"]
        if "category" in body:
            row.Category = body["category"] or None
        if "logo_path" in body:
            row.LogoPath = body["logo_path"] or None
        if "stream_url" in body:
            row.StreamURL = body["stream_url"] or None

    db.commit()
    return JSONResponse({"updated": len(rows), "station_name": rows[0].Station_name})


# ---------------------------------------------------------------------------
# API: Add new tower row to TBFPStation
# ---------------------------------------------------------------------------

@router.post("/api/map/tower")
async def api_map_tower_add(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Add a new tower record to TBFPStation."""
    import math as _math
    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    body = await request.json()
    required = ["tower_location", "lat_dms", "lon_dms", "lat_decimal",
                "lon_decimal", "freq_mhz", "erp_kw", "station_name"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return JSONResponse({"error": f"Missing: {', '.join(missing)}"}, status_code=400)

    erp = float(body["erp_kw"])
    radius = round(_math.min(28 * _math.sqrt(erp), 120), 2) if erp > 0 else 0.0

    # Next RowNo
    from sqlalchemy import func as _func
    max_row = db.query(_func.max(TBFPStation.RowNo)).scalar() or 0

    station = TBFPStation(
        RowNo=max_row + 1,
        Tower_location=body["tower_location"].strip().upper(),
        Province=body.get("province") or None,
        LatDMS=body["lat_dms"],
        LonDMS=body["lon_dms"],
        LatDecimal=float(body["lat_decimal"]),
        LonDecimal=float(body["lon_decimal"]),
        FreqMHz=float(body["freq_mhz"]),
        ErpKw=erp,
        Polarisation=body.get("polarisation") or None,
        Station_name=body["station_name"].strip(),
        LogoPath=body.get("logo_path") or None,
        StreamURL=body.get("stream_url") or None,
        Status=body.get("status") or "OPE",
        Category=body.get("category") or None,
        OnAirDate=body.get("on_air_date") or None,
        UnitIP=body.get("unit_ip") or None,
        Comments=body.get("comments") or None,
        RadiusKm=radius,
    )
    db.add(station)
    db.commit()
    db.refresh(station)
    return JSONResponse(station.to_geojson())


# ---------------------------------------------------------------------------
# API: Add new operational station to dbo.Station
# ---------------------------------------------------------------------------

@router.post("/api/map/operational-station")
async def api_map_operational_station_add(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Add a new operational station to dbo.Station (pipeline uses this)."""
    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    body = await request.json()
    if not body.get("station_name"):
        return JSONResponse({"error": "station_name required"}, status_code=400)

    from app.models.station import Station
    from sqlalchemy import text

    # Check for duplicate
    existing = db.query(Station).filter(
        Station.StationName == body["station_name"].strip()
    ).first()
    if existing:
        return JSONResponse(
            {"error": f"Station '{body['station_name']}' already exists"},
            status_code=409
        )

    station = Station(
        StationName=body["station_name"].strip(),
        StreamURL=body.get("stream_url") or None,
        IsActive=False,  # inactive until a service is registered on it
    )
    db.add(station)
    db.commit()
    db.refresh(station)
    return JSONResponse({
        "station_id":   station.StationID,
        "station_name": station.StationName,
        "stream_url":   station.StreamURL,
        "is_active":    station.IsActive,
    })


# ---------------------------------------------------------------------------
# API: Delete single TBFPStation row
# ---------------------------------------------------------------------------

@router.delete("/api/map/station/{station_id}")
def api_map_station_delete(
    station_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Delete a single TBFPStation row. Internal users only."""
    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    station = db.get(TBFPStation, station_id)
    if not station:
        return JSONResponse({"error": "Not found"}, status_code=404)

    name = station.Station_name
    tower = station.Tower_location
    db.delete(station)
    db.commit()
    return JSONResponse({"deleted": 1, "station_name": name, "tower": tower})


# ---------------------------------------------------------------------------
# API: Delete ALL TBFPStation rows for a Station_name
# ---------------------------------------------------------------------------

@router.delete("/api/map/program-delete")
def api_map_program_delete(
    station_name: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Delete all TBFPStation rows for a given Station_name. Internal users only."""
    if user.user_type.value != "internal":
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    rows = (
        db.query(TBFPStation)
        .filter(TBFPStation.Station_name == station_name)
        .all()
    )
    if not rows:
        return JSONResponse({"error": "No rows found"}, status_code=404)

    count = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return JSONResponse({"deleted": count, "station_name": station_name})


# ---------------------------------------------------------------------------
# Stream proxy — serves HTTP audio streams over HTTPS to avoid mixed content
# ---------------------------------------------------------------------------

from urllib.parse import urlparse

# Allow-list: known public HTTP stream domains from TBFPStation
_ALLOWED_STREAM_HOSTS = {
    "s7.voscast.com",
    "s8.voscast.com",
    "88.198.119.244",
    "zas4-5-7.ndx.co.za",
    "102.68.115.115",
    "stream2.luisterfm.co.za",
    "uk21freenew.listen2myradio.com",
    "116.202.241.212",
}

# WireGuard VPN subnet for Pi devices (10.100.100.0/24)
_WIREGUARD_SUBNET = "10.100.100."


def _is_allowed_host(hostname: str) -> bool:
    """Allow known public stream hosts OR any WireGuard VPN device."""
    if hostname in _ALLOWED_STREAM_HOSTS:
        return True
    if hostname.startswith(_WIREGUARD_SUBNET):
        return True
    return False


@router.get("/api/stream-proxy")
def stream_proxy(
    url: str,
    user: Annotated[User, Depends(get_current_user)],
):
    """
    Proxy an HTTP audio stream over HTTPS to avoid mixed-content browser blocks.
    Allows known public stream hosts and WireGuard VPN Pi devices (10.100.100.x).
    """
    import urllib.request
    from fastapi.responses import StreamingResponse

    try:
        parsed = urlparse(url)
    except Exception:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)

    if parsed.scheme != "http":
        return JSONResponse({"error": "Only HTTP streams need proxying"}, status_code=400)

    if not _is_allowed_host(parsed.hostname or ""):
        return JSONResponse({"error": "Stream host not permitted"}, status_code=403)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RadioMonitor/1.0",
                "Icy-MetaData": "0",
            }
        )
        response = urllib.request.urlopen(req, timeout=10)
        content_type = response.headers.get("Content-Type", "audio/mpeg")

        def stream_generator():
            try:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                response.close()

        return StreamingResponse(
            stream_generator(),
            media_type=content_type,
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            }
        )
    except Exception as e:
        import logging
        logging.getLogger("radiomonitor").warning(f"Stream proxy failed for {url}: {e}")
        return JSONResponse({"error": "Stream unavailable"}, status_code=502)
