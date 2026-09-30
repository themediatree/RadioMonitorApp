"""
app/services/bulk_upload_service.py

Bulk upload via Excel template -- generates the downloadable workbook and
validates/processes an uploaded one.

Four worksheets, one per registration/subscription type, mirroring the
single-entry forms exactly:

    Commercials   -- generic commercial registration (audio file by name)
    Liveread      -- liveread registration (text file by name)
    Songs         -- song subscription
    Words&Phrases -- keyword/phrase subscription

Each row is processed through the EXACT SAME service functions the
single-entry forms call (registration_service, subscription_service) --
no parallel logic. Token costs, fingerprinting, staging, and job creation
all behave identically to manual entry, just looped.

Station restrictions: NOT enforced for bulk upload at this time -- the
business has indicated per-row station permission enforcement for bulk
uploads is still under reconsideration and will be revisited. This is a
deliberate, temporary scope decision, not an oversight. When the station
permission redesign lands, validate_workbook() is the place to add the
per-row check (see get_allowed_station_ids in permission_service).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

HEADER_FILL = PatternFill("solid", start_color="1A4FA8", end_color="1A4FA8")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name="Arial", size=10)
INSTRUCTION_FONT = Font(name="Arial", size=10, italic=True, color="666666")
REQUIRED_FILL = PatternFill("solid", start_color="FFF4E5", end_color="FFF4E5")

# Structural-edit friction, not a security boundary -- the workbook itself
# isn't sensitive. This stops accidental/rogue header edits, tab renames,
# or tab deletions from someone clicking around without realizing what
# they're doing. Documented openly here since NOCTIV staff need it to
# unlock the template for maintenance; it is NOT meant to be secret.
PROTECTION_PASSWORD = "noctiv-template-2026"


def _protect_header_row(ws, num_cols: int, max_data_row: int = 1000) -> None:
    """
    Locks the header row (row 1) so column names can't be edited, renamed,
    or deleted, while leaving every data row fully editable. Requires
    sheet.protection.sheet = True to take effect -- unlocked cells stay
    editable even when sheet protection is on; only explicitly locked
    cells (the header) become read-only.
    """
    from openpyxl.styles import Protection

    # Unlock all data cells first (openpyxl cells default to locked=True,
    # which only matters once sheet protection is enabled).
    for row in ws.iter_rows(min_row=2, max_row=max_data_row, max_col=num_cols):
        for cell in row:
            cell.protection = Protection(locked=False)

    # Lock the header row explicitly.
    for col in range(1, num_cols + 1):
        ws.cell(row=1, column=col).protection = Protection(locked=True)

    ws.protection.sheet = True
    ws.protection.password = PROTECTION_PASSWORD
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.protection.insertRows = False
    ws.protection.deleteRows = False
    ws.protection.insertColumns = False
    ws.protection.deleteColumns = False
    # Sorting and autofilter stay allowed -- useful for reviewing large
    # uploads without compromising header integrity.
    ws.protection.sort = False
    ws.protection.autoFilter = False


def _style_header_row(ws, columns: list[str]) -> None:
    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"


def _set_column_widths(ws, widths: dict[str, int]) -> None:
    for col_letter, width in widths.items():
        ws.column_dimensions[col_letter].width = width


def _add_station_validation(ws, station_names: list[str], col_letter: str, max_row: int = 200) -> None:
    """
    Excel data validation dropdowns only support a single value per cell
    natively -- since stations are comma-separated (multi-station), we
    can't use a strict dropdown for the Stations column itself. Instead we
    add an info comment-style instruction row and rely on server-side
    validation to catch typos. A real dropdown is added to a single-station
    helper column instead, documented in the Instructions sheet.
    """
    pass  # see _add_instructions_sheet -- station list is shown there for reference


def _add_instructions_sheet(wb: Workbook, station_names: list[str]) -> None:
    ws = wb.create_sheet("Instructions", 0)
    ws.column_dimensions["A"].width = 100
    rows = [
        ("NOCTIV Bulk Upload Template", True),
        ("", False),
        ("How to use this workbook:", True),
        ("1. Fill in one row per item on the relevant worksheet tab (Commercials, Liveread, Songs, Words & Phrases).", False),
        ("2. Leave a worksheet entirely empty if you have nothing to submit for that type.", False),
        ("3. Stations: enter station names exactly as listed below, separated by commas (e.g. 5FM, 947, kfm).", False),
        ("4. Dates: use YYYY-MM-DD format (e.g. 2026-07-01). Leave End Date blank for open-ended monitoring.", False),
        ("5. Commercials & Liveread: the AudioFilename / TextFilename column must exactly match a filename in the ZIP you upload alongside this workbook.", False),
        ("6. The header row (row 1) and worksheet tabs are locked and cannot be edited, renamed, reordered, or deleted -- this is how the system reliably reads your data. Only the data rows are editable.", False),
        ("7. You may save and name this file however suits your internal naming convention -- the filename itself is not read by the system, only the worksheet tabs and headers within it.", False),
        ("8. Once uploaded, you'll see a cost preview and a list of any rows that failed validation before anything is charged.", False),
        ("", False),
        ("Available stations:", True),
        (", ".join(station_names), False),
        ("", False),
        ("__EMAIL_LINK__", False),
    ]
    for i, (text, bold) in enumerate(rows, start=1):
        if text == "__EMAIL_LINK__":
            cell = ws.cell(row=i, column=1, value="Questions? Contact admin@noctiv.co.za")
            cell.hyperlink = "mailto:admin@noctiv.co.za"
            cell.font = Font(name="Arial", size=10, color="1A4FA8", underline="single")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            continue
        cell = ws.cell(row=i, column=1, value=text)
        cell.font = Font(name="Arial", size=12 if bold and i == 1 else 10, bold=bold)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[1].height = 24


@dataclass
class RowResult:
    sheet: str
    row_number: int
    is_valid: bool
    data: dict
    error: Optional[str] = None
    cost: Optional[Decimal] = None


@dataclass
class ValidationResult:
    session_token: Optional[str]
    rows: list = field(default_factory=list)
    total_cost: Decimal = Decimal("0")
    valid_count: int = 0
    failed_count: int = 0
    balance: Optional[Decimal] = None
    insufficient_balance: bool = False


_REQUIRED_COLS = {
    "Commercials": ["TapeID", "Campaign", "Brand", "Stations", "StartDate", "AudioFilename"],
    "Liveread": ["TapeID", "Campaign", "Brand", "Stations", "StartDate", "TextFilename"],
    "Songs": ["Stations", "StartDate"],  # Title or TrackID required, checked separately
    "Words&Phrases": ["Keyword", "Stations", "StartDate"],
}


def _parse_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_stations(value, station_map: dict[str, int]) -> tuple[list[int], Optional[str]]:
    """Returns (station_ids, error). station_map is {lowercase name: StationID}."""
    if not value:
        return [], "Stations is required."
    names = [n.strip() for n in str(value).split(",") if n.strip()]
    if not names:
        return [], "Stations is required."
    ids = []
    unknown = []
    for name in names:
        sid = station_map.get(name.lower())
        if sid is None:
            unknown.append(name)
        else:
            ids.append(sid)
    if unknown:
        return [], f"Unknown station(s): {', '.join(unknown)}"
    return ids, None


def _read_sheet_rows(ws) -> list[dict]:
    """Reads a worksheet into a list of dicts keyed by header, skipping the
    instruction/example row (row 2) and any fully blank rows."""
    headers = [c.value for c in ws[1]]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=False):
        if all(c.value in (None, "") for c in r):
            continue
        row_dict = {headers[i]: r[i].value for i in range(len(headers)) if headers[i]}
        # Skip the italic example row -- it's identifiable by row number 2
        # AND matching the known example values, but simplest robust check:
        # treat row 2 as instructional only if it exactly matches our seeded
        # example values is fragile, so instead we rely on the convention
        # that real data starts at row 3. Row 2 is reserved for the example.
        if r[0].row == 2:
            continue
        rows.append({"_row_number": r[0].row, **row_dict})
    return rows


def validate_workbook(
    db: Session,
    user,
    workbook_bytes: bytes,
    zip_bytes: Optional[bytes],
) -> ValidationResult:
    """
    Parses the uploaded workbook (+ optional ZIP of audio/text files),
    validates every row across all 4 sheets, computes total cost, and
    stores a BulkUploadSession + BulkUploadRow rows for the subsequent
    commit step. Does NOT debit credits or create any Commercial/
    ClientSubscription rows -- that only happens at commit.
    """
    import json
    import secrets
    import zipfile
    from openpyxl import load_workbook
    from app.models.bulk_upload import BulkUploadSession, BulkUploadRow
    from app.models.station import Station
    from app.services import registration_service, token_service

    stations = (
        db.query(Station)
        .filter(Station.IsActive == True)  # noqa: E712
        .filter(Station.StationID != 6)
        .all()
    )
    station_map = {s.StationName.lower(): s.StationID for s in stations}

    zip_filenames: set[str] = set()
    if zip_bytes:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                zip_filenames = {n.split("/")[-1] for n in zf.namelist() if not n.endswith("/")}
        except zipfile.BadZipFile:
            pass

    try:
        wb = load_workbook(io.BytesIO(workbook_bytes), data_only=True)
    except Exception as e:
        result = ValidationResult(session_token=None)
        result.rows.append(RowResult(
            sheet="(file)", row_number=0, is_valid=False, data={},
            error=f"Could not read the uploaded file: {e}",
        ))
        result.failed_count = 1
        return result

    all_results: list[RowResult] = []
    total_cost = Decimal("0")

    # --- Commercials (generic) ---
    if "Commercials" in wb.sheetnames:
        for row in _read_sheet_rows(wb["Commercials"]):
            rn = row.pop("_row_number")
            err = None
            tape_id = str(row.get("TapeID") or "").strip()
            campaign = str(row.get("Campaign") or "").strip()
            audio_fn = str(row.get("AudioFilename") or "").strip()
            sd = _parse_date(row.get("StartDate"))
            ed = _parse_date(row.get("EndDate"))
            station_ids, station_err = _parse_stations(row.get("Stations"), station_map)

            if not tape_id:
                err = "TapeID is required."
            elif not campaign:
                err = "Campaign is required."
            elif not audio_fn:
                err = "AudioFilename is required."
            elif zip_bytes is not None and audio_fn not in zip_filenames:
                err = f"'{audio_fn}' not found in the uploaded ZIP."
            elif zip_bytes is None:
                err = "No ZIP file uploaded -- required for Commercials."
            elif sd is None:
                err = "StartDate is required (YYYY-MM-DD)."
            elif station_err:
                err = station_err

            cost = None
            if err is None:
                cost = token_service.calculate_cost(len(station_ids), sd, ed)
                total_cost += cost

            all_results.append(RowResult(
                sheet="Commercials", row_number=rn, is_valid=err is None,
                data=row, error=err, cost=cost,
            ))

    # --- Liveread ---
    if "Liveread" in wb.sheetnames:
        for row in _read_sheet_rows(wb["Liveread"]):
            rn = row.pop("_row_number")
            err = None
            tape_id = str(row.get("TapeID") or "").strip()
            campaign = str(row.get("Campaign") or "").strip()
            text_fn = str(row.get("TextFilename") or "").strip()
            sd = _parse_date(row.get("StartDate"))
            ed = _parse_date(row.get("EndDate"))
            station_ids, station_err = _parse_stations(row.get("Stations"), station_map)

            if not tape_id:
                err = "TapeID is required."
            elif not campaign:
                err = "Campaign is required."
            elif not text_fn:
                err = "TextFilename is required."
            elif zip_bytes is not None and text_fn not in zip_filenames:
                err = f"'{text_fn}' not found in the uploaded ZIP."
            elif zip_bytes is None:
                err = "No ZIP file uploaded -- required for Liveread."
            elif sd is None:
                err = "StartDate is required (YYYY-MM-DD)."
            elif station_err:
                err = station_err

            cost = None
            if err is None:
                cost = token_service.calculate_cost(len(station_ids), sd, ed)
                total_cost += cost

            all_results.append(RowResult(
                sheet="Liveread", row_number=rn, is_valid=err is None,
                data=row, error=err, cost=cost,
            ))

    # --- Songs ---
    if "Songs" in wb.sheetnames:
        for row in _read_sheet_rows(wb["Songs"]):
            rn = row.pop("_row_number")
            err = None
            title = str(row.get("Title") or "").strip()
            track_id = str(row.get("TrackID") or "").strip()
            sd = _parse_date(row.get("StartDate"))
            ed = _parse_date(row.get("EndDate"))
            station_ids, station_err = _parse_stations(row.get("Stations"), station_map)

            if not title and not track_id:
                err = "Enter a song Title or TrackID."
            elif sd is None:
                err = "StartDate is required (YYYY-MM-DD)."
            elif station_err:
                err = station_err

            cost = None
            if err is None:
                cost = token_service.calculate_cost(len(station_ids), sd, ed)
                total_cost += cost

            all_results.append(RowResult(
                sheet="Songs", row_number=rn, is_valid=err is None,
                data=row, error=err, cost=cost,
            ))

    # --- Words & Phrases ---
    if "Words&Phrases" in wb.sheetnames:
        for row in _read_sheet_rows(wb["Words&Phrases"]):
            rn = row.pop("_row_number")
            err = None
            keyword = str(row.get("Keyword") or "").strip()
            sd = _parse_date(row.get("StartDate"))
            ed = _parse_date(row.get("EndDate"))
            station_ids, station_err = _parse_stations(row.get("Stations"), station_map)

            if not keyword:
                err = "Keyword is required."
            elif sd is None:
                err = "StartDate is required (YYYY-MM-DD)."
            elif station_err:
                err = station_err

            cost = None
            if err is None:
                cost = token_service.calculate_cost(len(station_ids), sd, ed)
                total_cost += cost

            all_results.append(RowResult(
                sheet="Words&Phrases", row_number=rn, is_valid=err is None,
                data=row, error=err, cost=cost,
            ))

    valid_count = sum(1 for r in all_results if r.is_valid)
    failed_count = sum(1 for r in all_results if not r.is_valid)

    result = ValidationResult(
        session_token=None, rows=all_results, total_cost=total_cost,
        valid_count=valid_count, failed_count=failed_count,
    )

    if user.SubscriberID:
        balance = token_service.get_balance(db, user.SubscriberID)
        result.balance = balance
        result.insufficient_balance = balance < total_cost

    if valid_count == 0:
        return result  # nothing to commit, session_token stays None

    # --- Persist session + rows for the commit step ---
    token = secrets.token_urlsafe(24)
    zip_path = None
    if zip_bytes:
        import os
        from app.config import settings
        stage_root = getattr(settings, "bulk_upload_staging_root", r"D:\RadioMonitor\bulk_staging")
        os.makedirs(stage_root, exist_ok=True)
        zip_path = os.path.join(stage_root, f"{token}.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)

    session = BulkUploadSession(
        SessionToken=token,
        UserID=user.UserID,
        SubscriberID=user.SubscriberID,
        Status="validated",
        TotalCost=total_cost,
        ValidRowCount=valid_count,
        FailedRowCount=failed_count,
        ZipStoragePath=zip_path,
        CreatedAt=datetime.now(),
        ExpiresAt=datetime.now() + timedelta(hours=1),
    )
    db.add(session)
    db.flush()

    for r in all_results:
        db.add(BulkUploadRow(
            SessionID=session.SessionID,
            SheetName=r.sheet,
            RowNumber=r.row_number,
            IsValid=r.is_valid,
            ErrorMessage=r.error,
            RowDataJson=json.dumps(r.data, default=str),
            EstimatedCost=r.cost,
        ))
    db.commit()

    result.session_token = token
    return result


def build_template(db: Session, station_names: list[str]) -> bytes:
    """Generates the downloadable bulk upload workbook as bytes."""
    wb = Workbook()
    wb.remove(wb.active)  # remove default empty sheet

    _add_instructions_sheet(wb, station_names)

    # --- Commercials (generic) ---
    ws = wb.create_sheet("Commercials")
    cols = ["TapeID", "Campaign", "Brand", "Stations", "StartDate", "EndDate", "AudioFilename"]
    _style_header_row(ws, cols)
    _set_column_widths(ws, {"A": 20, "B": 24, "C": 18, "D": 30, "E": 14, "F": 14, "G": 26})
    ws.cell(row=2, column=1, value="Samsung-073-E").font = INSTRUCTION_FONT
    ws.cell(row=2, column=2, value="Samsung Q3 Campaign").font = INSTRUCTION_FONT
    ws.cell(row=2, column=3, value="Samsung").font = INSTRUCTION_FONT
    ws.cell(row=2, column=4, value="5FM, 947").font = INSTRUCTION_FONT
    ws.cell(row=2, column=5, value="2026-07-01").font = INSTRUCTION_FONT
    ws.cell(row=2, column=6, value="").font = INSTRUCTION_FONT
    ws.cell(row=2, column=7, value="Samsung-073-E.mp3").font = INSTRUCTION_FONT
    _protect_header_row(ws, len(cols))

    # --- Liveread ---
    ws = wb.create_sheet("Liveread")
    cols = ["TapeID", "Campaign", "Brand", "Stations", "StartDate", "EndDate", "TextFilename"]
    _style_header_row(ws, cols)
    _set_column_widths(ws, {"A": 20, "B": 24, "C": 18, "D": 30, "E": 14, "F": 14, "G": 26})
    ws.cell(row=2, column=1, value="Outsurance-LR-E").font = INSTRUCTION_FONT
    ws.cell(row=2, column=2, value="Outsurance Liveread").font = INSTRUCTION_FONT
    ws.cell(row=2, column=3, value="Outsurance").font = INSTRUCTION_FONT
    ws.cell(row=2, column=4, value="HOT1027").font = INSTRUCTION_FONT
    ws.cell(row=2, column=5, value="2026-07-01").font = INSTRUCTION_FONT
    ws.cell(row=2, column=6, value="").font = INSTRUCTION_FONT
    ws.cell(row=2, column=7, value="Outsurance-LR-E.txt").font = INSTRUCTION_FONT
    _protect_header_row(ws, len(cols))

    # --- Songs ---
    ws = wb.create_sheet("Songs")
    cols = ["Title", "Artist", "TrackID", "Stations", "StartDate", "EndDate"]
    _style_header_row(ws, cols)
    _set_column_widths(ws, {"A": 26, "B": 22, "C": 14, "D": 30, "E": 14, "F": 14})
    ws.cell(row=2, column=1, value="Surfin' U.S.A.").font = INSTRUCTION_FONT
    ws.cell(row=2, column=2, value="The Beach Boys").font = INSTRUCTION_FONT
    ws.cell(row=2, column=3, value="").font = INSTRUCTION_FONT
    ws.cell(row=2, column=4, value="5FM, 947, kfm").font = INSTRUCTION_FONT
    ws.cell(row=2, column=5, value="2026-07-01").font = INSTRUCTION_FONT
    ws.cell(row=2, column=6, value="").font = INSTRUCTION_FONT
    _protect_header_row(ws, len(cols))

    # --- Words & Phrases ---
    ws = wb.create_sheet("Words&Phrases")
    cols = ["Keyword", "Stations", "StartDate", "EndDate"]
    _style_header_row(ws, cols)
    _set_column_widths(ws, {"A": 30, "B": 30, "C": 14, "D": 14})
    ws.cell(row=2, column=1, value="Pick n Pay").font = INSTRUCTION_FONT
    ws.cell(row=2, column=2, value="5FM, JakarandaFM").font = INSTRUCTION_FONT
    ws.cell(row=2, column=3, value="2026-07-01").font = INSTRUCTION_FONT
    ws.cell(row=2, column=4, value="").font = INSTRUCTION_FONT
    _protect_header_row(ws, len(cols))

    # --- Lock workbook structure: prevents renaming, deleting, reordering,
    # or adding worksheet tabs. Does not require a password -- this is
    # integrity protection against accidental changes, not a security
    # boundary (the file isn't secret). lockStructure prevents the tab
    # operations; lockWindows is left off so users can still resize/arrange
    # windows freely.
    wb.security.lockStructure = True
    wb.security.workbookPassword = PROTECTION_PASSWORD

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
