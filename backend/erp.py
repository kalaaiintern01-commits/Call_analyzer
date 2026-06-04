"""Kala Genset ERP integration — read-only access to the dbo.Enquiry table.

Connects to the production SQL Server (`ERPAI` DB) using `pyodbc`, exposes a
single helper `fetch_enquiries(month=...)` that returns the customer name +
phone + a few other fields for enquiries in a given month. Used by the
"Call Customers" frontend tab to drive outbound AI agent calls.

Connection details come from env vars (see backend/.env):
    ERP_SQL_SERVER, ERP_SQL_DATABASE, ERP_SQL_USERNAME, ERP_SQL_PASSWORD,
    ERP_SQL_DRIVER

Notes
-----
* The ERP `Enquiry` table has ~310K rows — always filter by month.
* Phone numbers in the DB are inconsistently formatted ('9008666260',
  '?+91 96571 81016?', etc.). We normalize them at read time to a canonical
  E.164-ish form so the outbound call endpoint can use them directly.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Optional

import pyodbc

# ── Connections ───────────────────────────────────────────────────────────
#
# We maintain TWO separate pyodbc connections:
#
#   _erp_conn       → ERPAI database (read-only access to dbo.Enquiry)
#   _summary_conn   → call_analyzer database (read+write on dbo.AICallSummary)
#
# Same SQL Server, same login (call_analyzer), different default DB. Keeping
# them as separate connection objects means our summary writes can never
# accidentally touch the production ERPAI database — even a malformed query
# would fail with "object not found" rather than corrupting enquiry data.
#
# pyodbc connections aren't thread-safe at the connection level, so each has
# its own lock. For our load (<1 query/sec) this is plenty.

_erp_conn: Optional[pyodbc.Connection] = None
_erp_lock = threading.Lock()

_summary_conn: Optional[pyodbc.Connection] = None
_summary_lock = threading.Lock()


def _build_conn_string(database: str) -> str:
    """Build a pyodbc connection string for a specific database on the ERP server."""
    server = os.getenv("ERP_SQL_SERVER", "")
    username = os.getenv("ERP_SQL_USERNAME", "")
    password = os.getenv("ERP_SQL_PASSWORD", "")
    driver = os.getenv("ERP_SQL_DRIVER", "ODBC Driver 17 for SQL Server")

    if not (server and database and username and password):
        raise RuntimeError(
            f"ERP SQL Server env vars missing (server/db/user/pass) for database {database!r}"
        )

    return (
        f"Driver={{{driver}}};"
        f"Server={server};Database={database};"
        f"UID={username};PWD={password};"
        # TLS-on with cert pinning skipped — server uses a self-signed cert.
        # Acceptable because the DB is on a private/firewalled IP and the password
        # gates access. Re-evaluate when the cert is properly provisioned.
        "Encrypt=yes;TrustServerCertificate=yes;"
        "Connection Timeout=10;"
    )


def _get_conn_for(database: str, conn_ref: list, lock: threading.Lock) -> pyodbc.Connection:
    """Generic shared-connection getter with liveness check + auto-reconnect.

    `conn_ref` is a single-element list so we can mutate the outer slot.
    """
    with lock:
        if conn_ref[0] is None:
            conn_ref[0] = pyodbc.connect(_build_conn_string(database), autocommit=True)
            return conn_ref[0]
        try:
            conn_ref[0].cursor().execute("SELECT 1").fetchone()
            return conn_ref[0]
        except pyodbc.Error:
            try:
                conn_ref[0].close()
            except Exception:
                pass
            conn_ref[0] = pyodbc.connect(_build_conn_string(database), autocommit=True)
            return conn_ref[0]


# Use mutable list wrappers so _get_conn_for can rebind on reconnect.
_erp_conn_ref: list = [None]
_summary_conn_ref: list = [None]


def _get_erp_conn() -> pyodbc.Connection:
    """ERPAI database — for dbo.Enquiry reads."""
    return _get_conn_for(
        os.getenv("ERP_SQL_DATABASE", "ERPAI"),
        _erp_conn_ref,
        _erp_lock,
    )


def _get_summary_conn() -> pyodbc.Connection:
    """call_analyzer database — for dbo.AICallSummary read/writes."""
    return _get_conn_for(
        os.getenv("ERP_SUMMARY_DATABASE", "call_analyzer"),
        _summary_conn_ref,
        _summary_lock,
    )


def health_check() -> dict[str, Any]:
    """Return whether both ERP databases are reachable.

    Frontend shows a green/red badge based on this — both must be reachable
    for a fully-functioning system.
    """
    out: dict[str, Any] = {}
    for name, getter in (("erp", _get_erp_conn), ("summary", _get_summary_conn)):
        try:
            conn = getter()
            lock = _erp_lock if name == "erp" else _summary_lock
            with lock:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
            out[name] = "ok"
        except Exception as e:
            out[name] = f"error: {type(e).__name__}: {e}"

    overall = "ok" if all(v == "ok" for v in out.values()) else "error"
    return {"status": overall, **out}


# ── Phone normalisation ───────────────────────────────────────────────────
#
# The ERP stores phone numbers in many shapes — we've seen:
#   '9008666260'           (clean 10-digit)
#   '?+91 96571 81016?'    (curly question marks + spaces from an Excel paste)
#   '+919876543210'        (E.164)
#   '02012345678'          (landline)
#   ''                     (empty)
# Normalise to digits-only and, when it looks like an Indian mobile, prepend +91.

_PHONE_DIGIT_RE = re.compile(r"\D+")


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Return a callable E.164 number, or None if not a usable mobile.

    Heuristics:
    * Strip everything that isn't a digit.
    * If 10 digits and starts with 6/7/8/9 → assume Indian mobile, prepend +91.
    * If 12 digits and starts with 91     → '+' prefix.
    * If 11 digits and starts with 0      → drop the 0, treat as 10-digit.
    * Anything else → return None (the frontend will hide the call button).
    """
    if not raw:
        return None
    digits = _PHONE_DIGIT_RE.sub("", raw)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return "+" + digits
    # Doesn't match a callable mobile shape (landline, foreign, garbage). Skip.
    return None


# ── Queries ───────────────────────────────────────────────────────────────

# Columns we project. Keep this list small — the frontend only needs identity
# + a callable number. Mobile is preferred over office phone for the agent's
# follow-up call.
_ENQUIRY_COLUMNS = """
    EnqNo, Dt, CustName, ContactPerson,
    MobileNo, OfficePhNo, EMailID,
    City, State, ENQStatus, Active
""".strip()


def fetch_enquiries(
    month: Optional[str] = None,
    limit: int = 5000,
    only_with_phone: bool = True,
) -> list[dict[str, Any]]:
    """Return NEW/unverified enquiries from dbo.Enquiry for the given month, newest first.

    "New / unverified" means `ConfMobile = 'N'` — the customer's mobile hasn't
    been confirmed by the sales team yet, so these are leads the AI agent
    should call back. Once a sales rep confirms the mobile (ConfMobile flips
    to 'Y'), the row drops out of this list.

    Args:
        month: 'YYYY-MM'. If None, defaults to current month.
        limit: cap on rows returned (default 5000 — well above typical monthly
            volume after the ConfMobile='N' filter, which is much smaller than
            the total ~1300 enquiries/month).
        only_with_phone: if True (default), drop rows where we can't extract a
            callable mobile number — the frontend can't dial those anyway.

    Returns dicts with these keys (frontend-friendly camelCase-ish):
        enquiry_no, enquiry_date, customer_name, contact_person,
        mobile, office_phone, email, city, state, status, active, callable_phone
    """
    if month is None:
        now = datetime.now()
        month = f"{now.year:04d}-{now.month:02d}"

    try:
        year_s, month_s = month.split("-")
        year, month_n = int(year_s), int(month_s)
        if not (1 <= month_n <= 12):
            raise ValueError("month out of range")
    except Exception:
        raise ValueError(f"invalid month format: {month!r} (expected 'YYYY-MM')")

    conn = _get_erp_conn()
    with _erp_lock:
        cur = conn.cursor()
        # ConfMobile is char(1) so values come back space-padded ('N '). Use
        # LTRIM(RTRIM(...)) for compat with SQL Server <2017 (older boxes
        # don't have TRIM()).
        cur.execute(
            f"""
            SELECT TOP (?) {_ENQUIRY_COLUMNS}
            FROM dbo.Enquiry
            WHERE Dt >= DATEFROMPARTS(?, ?, 1)
              AND Dt <  DATEADD(MONTH, 1, DATEFROMPARTS(?, ?, 1))
              AND Active = 1
              AND LTRIM(RTRIM(ConfMobile)) = 'N'
            ORDER BY Dt DESC
            """,
            limit, year, month_n, year, month_n,
        )
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        mobile = (row.MobileNo or "").strip()
        office = (row.OfficePhNo or "").strip()
        callable_phone = normalize_phone(mobile) or normalize_phone(office)
        if only_with_phone and not callable_phone:
            continue

        out.append({
            "enquiry_no": (row.EnqNo or "").strip(),
            "enquiry_date": row.Dt.isoformat() if row.Dt else None,
            "customer_name": (row.CustName or "").strip(),
            "contact_person": (row.ContactPerson or "").strip(),
            "mobile": mobile,
            "office_phone": office,
            "email": (row.EMailID or "").strip(),
            "city": (row.City or "").strip(),
            "state": (row.State or "").strip(),
            "status": (row.ENQStatus or "").strip(),
            "active": bool(row.Active),
            "callable_phone": callable_phone,
        })

    return out


def fetch_enquiry(enquiry_no: str) -> Optional[dict[str, Any]]:
    """Fetch a single enquiry by EnqNo (for detail/lookup)."""
    conn = _get_erp_conn()
    with _erp_lock:
        cur = conn.cursor()
        cur.execute(
            f"SELECT TOP 1 {_ENQUIRY_COLUMNS} FROM dbo.Enquiry WHERE EnqNo = ?",
            enquiry_no,
        )
        row = cur.fetchone()
    if not row:
        return None
    mobile = (row.MobileNo or "").strip()
    office = (row.OfficePhNo or "").strip()
    return {
        "enquiry_no": (row.EnqNo or "").strip(),
        "enquiry_date": row.Dt.isoformat() if row.Dt else None,
        "customer_name": (row.CustName or "").strip(),
        "contact_person": (row.ContactPerson or "").strip(),
        "mobile": mobile,
        "office_phone": office,
        "email": (row.EMailID or "").strip(),
        "city": (row.City or "").strip(),
        "state": (row.State or "").strip(),
        "status": (row.ENQStatus or "").strip(),
        "active": bool(row.Active),
        "callable_phone": normalize_phone(mobile) or normalize_phone(office),
    }


# ── AI Call Summary persistence (dbo.AICallSummary) ───────────────────────
# Reads/writes the table created by the DBA (see SSMS script in chat history).
# Stores both flat columns (for sales filtering) AND a raw_payload JSON blob
# (for Phase 2 dynamic Q&A logs and other variable structure).

_SUMMARY_COLUMNS = [
    "call_uuid", "enq_no", "from_number", "to_number", "is_outbound",
    "customer_name", "designation", "company_name", "business_type", "location",
    "purpose", "new_or_replacement",
    "capacity_kva", "load_calculated",
    "phase", "fuel_type", "amf_required", "canopy_required",
    "timeline", "site_visit_ok", "competitor_quotes_received",
    "budget_range", "callback_number", "email",
    "language_used", "hot_lead", "call_summary",
    "raw_payload",
]


def _to_bit(v: Any) -> Optional[int]:
    """SQL Server BIT column expects 0/1 or NULL. Anything truthy → 1, falsy → 0,
    None/missing → NULL."""
    if v is None:
        return None
    return 1 if v else 0


def insert_or_update_summary(
    *,
    call_uuid: str,
    enq_no: Optional[str] = None,
    from_number: Optional[str] = None,
    to_number: Optional[str] = None,
    is_outbound: bool = False,
    fields: dict[str, Any],
) -> int:
    """Persist an AI-captured call summary. Returns the row's id.

    Idempotent — if a row already exists with this call_uuid, UPDATEs it
    instead of inserting a duplicate. (The LLM occasionally calls save_lead
    twice in one conversation — once early with `{}` to "register", then
    again at end with the full fields. We keep only the latest version.)

    `fields` is the full dict the LLM passed to the save_lead tool. We pull
    the standard columns out for indexed query access, and stash the entire
    dict in `raw_payload` as JSON so Phase 2 fields (interaction_log,
    competitor_brands array, etc.) are preserved without schema churn.
    """
    raw_payload = json.dumps(fields or {}, ensure_ascii=False, default=str)

    def g(k):  # noqa: short helper
        return (fields or {}).get(k)

    flat_args = (
        enq_no, from_number, to_number, _to_bit(is_outbound),
        g("customer_name"), g("designation"), g("company_name"), g("business_type"), g("location"),
        g("purpose"), g("new_or_replacement"),
        g("capacity_kva"), g("load_calculated"),
        g("phase"), g("fuel_type"), _to_bit(g("amf_required")), g("canopy_required"),
        g("timeline"), _to_bit(g("site_visit_ok")), _to_bit(g("competitor_quotes_received")),
        g("budget_range"), g("callback_number"), g("email"),
        g("language_used"), _to_bit(g("hot_lead")), g("call_summary"),
        raw_payload,
    )

    conn = _get_summary_conn()
    with _summary_lock:
        cur = conn.cursor()
        # Check for existing row with this call_uuid (LLM sometimes double-saves)
        cur.execute("SELECT id FROM dbo.AICallSummary WHERE call_uuid = ?", call_uuid)
        existing = cur.fetchone()
        if existing:
            existing_id = int(existing[0])
            cur.execute("""
                UPDATE dbo.AICallSummary
                SET enq_no = ?, from_number = ?, to_number = ?, is_outbound = ?,
                    customer_name = ?, designation = ?, company_name = ?, business_type = ?, location = ?,
                    purpose = ?, new_or_replacement = ?,
                    capacity_kva = ?, load_calculated = ?,
                    phase = ?, fuel_type = ?, amf_required = ?, canopy_required = ?,
                    timeline = ?, site_visit_ok = ?, competitor_quotes_received = ?,
                    budget_range = ?, callback_number = ?, email = ?,
                    language_used = ?, hot_lead = ?, call_summary = ?,
                    raw_payload = ?
                WHERE id = ?
            """, *flat_args, existing_id)
            return existing_id

        cur.execute("""
            INSERT INTO dbo.AICallSummary (
                call_uuid, enq_no, from_number, to_number, is_outbound,
                customer_name, designation, company_name, business_type, location,
                purpose, new_or_replacement,
                capacity_kva, load_calculated,
                phase, fuel_type, amf_required, canopy_required,
                timeline, site_visit_ok, competitor_quotes_received,
                budget_range, callback_number, email,
                language_used, hot_lead, call_summary,
                raw_payload
            )
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?)
        """, call_uuid, *flat_args)
        new_id = cur.fetchone()[0]
        return int(new_id)


def fetch_summaries(
    month: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return AI-captured call summaries, newest first.

    Args:
        month: 'YYYY-MM' to filter by captured_at. None = all months.
        limit: max rows.

    Returns dicts shaped to match the frontend (call_uuid as id, captured_at,
    from/to numbers, and a nested `fields` dict with everything from
    raw_payload — same shape the old in-memory store produced).
    """
    where_clause = ""
    args: list[Any] = [limit]
    if month:
        try:
            year_s, month_s = month.split("-")
            year, month_n = int(year_s), int(month_s)
            if not (1 <= month_n <= 12):
                raise ValueError("month out of range")
        except Exception:
            raise ValueError(f"invalid month format: {month!r} (expected 'YYYY-MM')")
        where_clause = (
            "WHERE captured_at >= DATEFROMPARTS(?, ?, 1) "
            "AND captured_at <  DATEADD(MONTH, 1, DATEFROMPARTS(?, ?, 1))"
        )
        args.extend([year, month_n, year, month_n])

    conn = _get_summary_conn()
    with _summary_lock:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT TOP (?)
                id, call_uuid, enq_no, captured_at,
                from_number, to_number, is_outbound,
                raw_payload
            FROM dbo.AICallSummary
            {where_clause}
            ORDER BY captured_at DESC
        """, *args)
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        # raw_payload holds the full LLM args; the flat columns are a denormalised
        # view of the same data. Return raw_payload as `fields` so the frontend's
        # existing `lead.fields.customer_name` style access keeps working.
        try:
            fields = json.loads(r.raw_payload) if r.raw_payload else {}
        except Exception:
            fields = {}
        out.append({
            "id": r.call_uuid,              # frontend uses .id as a row key
            "row_id": int(r.id),
            "call_uuid": r.call_uuid,
            "enq_no": r.enq_no,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
            "from_number": r.from_number or "",
            "to_number": r.to_number or "",
            "is_outbound": bool(r.is_outbound),
            "fields": fields,
        })
    return out
