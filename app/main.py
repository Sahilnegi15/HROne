"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
from datetime import datetime, timedelta, timezone ,date,time
from typing import Optional , Literal
from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException ,Query
from pydantic import BaseModel ,ConfigDict, Field
from pymongo import MongoClient , DESCENDING, ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo import ReturnDocument


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_indexes()   # runs once when the server starts
    yield              # app serves requests here


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan,)


# changes by me --------------------------
UTC = timezone.utc
PRESENT_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
VALID_STATUSES = PRESENT_STATUSES | {"ABSENT", "LEAVE"}






## create index 
def create_indexes():
    emp = db.employees
    att = db.attendance_logs

    # ---------- employees ----------
    # Unique emp_code: concurrent duplicate creates -> exactly one 201.
    # Also serves GET /employees sorted by emp_code, and every emp_code lookup.
    emp.create_index([("emp_code", ASCENDING)], unique=True, name="uniq_emp_code")

    # GET /employees?department=X (sorted by emp_code)
    emp.create_index([("department", ASCENDING), ("emp_code", ASCENDING)],
                     name="emp_dept_code")

    # Headcount (R9): department + joined_on <= date (summary, trend)
    emp.create_index([("department", ASCENDING), ("joined_on", ASCENDING)],
                     name="emp_dept_joined")

    # Department summary with no department filter: joined_on <= month_end
    emp.create_index([("joined_on", ASCENDING)], name="emp_joined")

    # ---------- attendance_logs ----------
    # One record per (emp_code, date): makes punch-in race-safe (exactly one 201).
    # Also serves employee monthly (emp_code + date range), the $lookup in the
    # department summary, PATCH lookup, and GET /attendance?emp_code=...
    att.create_index([("emp_code", ASCENDING), ("date", ASCENDING)],
                     unique=True, name="uniq_emp_date")

    # Punch-out: latest record with punch_in <= punched_at, sorted desc
    att.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)],
                     name="att_emp_punchin")

    # GET /attendance default: sort date desc, emp_code asc (+ date range),
    # and the daily trend date-range scan
    att.create_index([("date", DESCENDING), ("emp_code", ASCENDING)],
                     name="att_date_emp")

    # GET /attendance?status=... with the same sort
    att.create_index([("status", ASCENDING), ("date", DESCENDING), ("emp_code", ASCENDING)],
                     name="att_status_date_emp")

    # Late leaderboard: date range + late_minutes > 0, grouped by emp_code
    att.create_index([("date", ASCENDING), ("late_minutes", ASCENDING), ("emp_code", ASCENDING)],
                     name="att_date_late_emp")
# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def truncate_seconds(ts: datetime) -> datetime:
    return ts.replace(microsecond=0)
# there is a problem with the incorrect overnight shifts
# shift_start → calculates how late the employee is.
# shift_end → helps determine the correct shift date for overnight shifts.
def compute_late_minutes(punch_in: datetime, shift_start: str,shift_end:str) -> int:
    # h, m = map(int, shift_start.split(":"))
    # start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    # minutes = int((punch_in - start).total_seconds() / 60)
    # return minutes if minutes > 10 else 0
    punch_in = truncate_seconds(punch_in.astimezone(IST))

    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))

    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em

    # For an overnight shift, early-morning punches
    # belong to the shift that started the previous day.
    shift_date = punch_in.date()

    if end_minutes <= start_minutes:
        punch_minutes = punch_in.hour * 60 + punch_in.minute
        if punch_minutes < end_minutes:
            shift_date -= timedelta(days=1)

    start = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        sh,
        sm,
        tzinfo=IST
    )

    elapsed_seconds = (punch_in - start).total_seconds()

    if elapsed_seconds <= 10 * 60:
        return 0

    return int(elapsed_seconds // 60)



# def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
#     return round((punch_out - punch_in).total_seconds() / 3600, 2)
def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime
) -> float:
    punch_in = truncate_seconds(punch_in)
    punch_out = truncate_seconds(punch_out)

    seconds = int((punch_out - punch_in).total_seconds())

    hours = Decimal(seconds) / Decimal(3600)

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )

# we also need shift end in this function to determine the which shift it is 


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str
) -> int:
    punch_out = truncate_seconds(punch_out.astimezone(IST))

    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))

    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em

    shift_date = datetime.fromisoformat(date_str).date()

    # Overnight shift ends on the following calendar day.
    end_date = shift_date
    if end_minutes <= start_minutes:
        end_date += timedelta(days=1)

    shift_end_dt = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        eh,
        em,
        tzinfo=IST
    )

    elapsed_seconds = (punch_out - shift_end_dt).total_seconds()

    # Overtime counts only when at least 30 minutes.
    if elapsed_seconds < 30 * 60:
        return 0

    return int(elapsed_seconds // 60)

# def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
#     h, m = map(int, shift_end.split(":"))
#     end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
#     return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# User Build Helpers
# --------------------------------------------------------------------------- #
# def truncate_seconds(value: datetime) -> datetime:
#     """R1: Discard fractional seconds before calculation/storage."""
#     return value.replace(microsecond=0)


def parse_timestamp(timestamp: Optional[int]) -> datetime:
    """Request timestamps are Unix epoch seconds; omitted means now."""
    if timestamp is None:
        return truncate_seconds(datetime.now(UTC))
    try:
        return truncate_seconds(datetime.fromtimestamp(timestamp, tz=UTC))
    except (ValueError, OverflowError, OSError):
        raise HTTPException(422, "Invalid punched_at Unix timestamp")


def as_ist(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # MongoDB values should be timezone-aware due to tz_aware=True.
        # Treat naive values as UTC only for compatibility with legacy docs.
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def round_half_up(value, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    )


def validate_hhmm(value: str, field_name: str) -> str:
    try:
        datetime.strptime(value, "%H:%M")
    except (ValueError, TypeError):
        raise HTTPException(422, f"{field_name} must use HH:MM format")
    return value


def get_shift_datetimes(punch_in: datetime, employee: dict):
    """
    R1: Return the scheduled shift start/end in IST.

    For an overnight shift (shift_end <= shift_start), an early-morning
    punch-in before shift_end belongs to the shift started the previous day.
    """
    local_in = as_ist(truncate_seconds(punch_in))
    start_h, start_m = map(int, employee["shift_start"].split(":"))
    end_h, end_m = map(int, employee["shift_end"].split(":"))

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    overnight = end_minutes <= start_minutes

    shift_date = local_in.date()
    local_minutes = local_in.hour * 60 + local_in.minute

    if overnight and local_minutes < end_minutes:
        shift_date -= timedelta(days=1)

    shift_start = datetime.combine(
        shift_date, time(start_h, start_m), tzinfo=IST
    )
    end_date = shift_date + timedelta(days=1) if overnight else shift_date
    shift_end = datetime.combine(
        end_date, time(end_h, end_m), tzinfo=IST
    )
    return shift_start, shift_end


def compute_half_day(work_hours: float) -> bool:
    """R5: True if rounded work_hours is strictly less than 4.50."""
    return Decimal(str(work_hours)) < Decimal("4.50")


def is_present_status(status: str) -> bool:
    """R6: Only these statuses count as presence."""
    return status.upper() in PRESENT_STATUSES


def count_weekdays(start: date, end: date) -> int:
    """Count Monday-Friday inclusively; no holiday calendar (R7)."""
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def attendance_date(punch_in: datetime) -> str:
    """R1: Attendance date is the IST date of punch_in."""
    return as_ist(truncate_seconds(punch_in)).date().isoformat()


def serialize_doc(doc):
    if doc is None:
        return None

    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))

    for key, value in doc.items():
        if isinstance(value, datetime):
            # PyMongo returns naive datetimes as UTC by default.
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            doc[key] = value.astimezone(IST).isoformat(
                timespec="seconds"
            )

    return doc


def validate_status(status: str) -> str:
    normalized = status.upper()
    if normalized not in VALID_STATUSES:
        raise HTTPException(
            422,
            "status must be PRESENT, WFH, ON_DUTY, ABSENT, or LEAVE",
        )
    return normalized


def employee_or_404(emp_code: str) -> dict:
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Unknown employee")
    return employee


def get_period_dates(start_date: str, end_date: str):
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(422, "Dates must use YYYY-MM-DD format")
    if end < start:
        raise HTTPException(422, "end_date must be on or after start_date")
    return start, end

def normalize_datetime(value):
    if isinstance(value, (int, float)):
        # Validate EpochMillis range
        if not 100000000000 <= value <= 4102444800000:
            raise HTTPException(
                status_code=422,
                detail="Timestamp must be valid EpochMillis"
            )

        return datetime.fromtimestamp(
            value / 1000,
            tz=timezone.utc
        )

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)

    raise HTTPException(
        status_code=422,
        detail="Invalid timestamp format"
    )





#----------API DATA HANDLE ---------------

def epoch_millis_to_datetime(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(422, "Invalid EpochMillis timestamp")


def datetime_to_epoch_millis(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must be timezone-aware")

    return int(value.timestamp() * 1000)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str
    created_at: int # this is missing 


# class PunchInIn(BaseModel):
#     emp_code: str
#     punched_at: Optional[datetime] = None
#     status: str = "PRESENT"


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #


@app.get("/health")
def health():
    return {"status": "ok"}



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    # doc["created_at"] = datetime.now()
    # Convert EpochMillis to Python datetime (BSON-compatible)
    doc["created_at"] = datetime.fromtimestamp(
        body.created_at / 1000,
        tz=timezone.utc
    )
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1,ge=1), page_size: int = Query(20,ge=1 ,le=100)):
    q = {}
    if department:
        q["department"] = department
    # skip = page * page_size   it skips first  20  employeee
    skip = (page - 1) * page_size  
    # we have to provide q to filter out only the required department 
    # total = db.employees.count_documents({})
    total = db.employees.count_documents(q)

    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    # ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
    # d = ts.date().isoformat()
    if emp is None:
        raise HTTPException(404, "Employee not found")

    # Convert Unix timestamp in milliseconds to IST.
    if body.punched_at is not None:
         ts = normalize_datetime(body.punched_at)
    else:
         ts = normalize_datetime(datetime.now(IST))

    # Truncate to whole seconds.
    # ts = ts.replace(microsecond=0)

    # Attendance date must be the IST calendar date.
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"],emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1 ,ge=1),
    page_size: int = Query(20, ge=1,le=100),
):
    q = {}

     # Validate date format and range.
    try:
        start_date = date.fromisoformat(date_from) if date_from else None
        end_date = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="date_from must be less than or equal to date_to"
        )



    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    # docs = list(db.attendance_logs.find(q))    it take too much time 
    # docs.sort(key=lambda d: d["date"], reverse=True)
    # total = len(docs)   it is inefficient 

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    docs = list(
        db.attendance_logs.find(q, {"_id": 1, "emp_code": 1, "date": 1, "status": 1, "punch_in": 1, "punch_out": 1, "work_hours": 1, "late_minutes": 1, "overtime_minutes": 1, "half_day": 1, "history": 1})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    # total = db.attendance_logs.count_documents(q)

    # page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in docs:   # loop in docs not in page size
        d["id"] = str(d.pop("_id"))
    return {"items": docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #


#----------------------------------------------------------------------------#
#   POST  /attendance/punch-out
#----------------------------------------------------------------------------#
class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )




@app.post("/attendance/punch-out")
def punch_out(data: PunchOutIn):
    # 1. Find the employee using emp_code, not MongoDB _id.
    employee = employee_or_404(data.emp_code)

    # 2. Convert EpochMillis to a UTC-aware datetime.
    if data.punched_at is not None:
        punched_at = epoch_millis_to_datetime(data.punched_at)
    else:
        punched_at = datetime.now(UTC).replace(microsecond=0)

    # 3. Find the latest punch-in at or before the punch-out time.
    record = db.attendance_logs.find_one(
        {
            "emp_code": data.emp_code,
            "punch_in": {"$lte": punched_at},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found",
        )

    # 4. Normalize the stored punch-in timestamp to UTC.
    punch_in_time = record["punch_in"]

    if punch_in_time.tzinfo is None or punch_in_time.utcoffset() is None:
        punch_in_time = punch_in_time.replace(tzinfo=UTC)
    else:
        punch_in_time = punch_in_time.astimezone(UTC)

    punch_in_time = punch_in_time.replace(microsecond=0)

    # 5. Reject a record that has already been punched out.
    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record is already punched out",
        )

    # 6. Validate the elapsed time.
    elapsed_seconds = int(
        (punched_at - punch_in_time).total_seconds()
    )

    if elapsed_seconds <= 0:
        raise HTTPException(
            status_code=422,
            detail="punched_at must be later than punch_in",
        )

    if elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    # 7. Convert timestamps to IST for shift calculations.
    punch_in_ist = punch_in_time.astimezone(IST)
    punched_at_ist = punched_at.astimezone(IST)

    # 8. Calculate the scheduled shift end, including overnight shifts.
    _, scheduled_end = get_shift_datetimes(
        punch_in_ist,
        employee,
    )

    # 9. Calculate work hours using the existing helper.
    work_hours = compute_work_hours(
        punch_in_time,
        punched_at,
    )

    # 10. Calculate overtime.
    overtime_seconds = (
        punched_at_ist - scheduled_end
    ).total_seconds()

    overtime_minutes = (
        int(overtime_seconds // 60)
        if overtime_seconds >= 30 * 60
        else 0
    )

    # 11. Determine whether the employee worked a half day.
    half_day = compute_half_day(work_hours)

    # 12. Atomically update only if punch_out is still null or missing.
    #     This prevents two simultaneous punch-out requests from succeeding.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_in": punch_in_time,
                "punch_out": punched_at,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
                "updated_at": datetime.now(UTC).replace(
                    microsecond=0
                ),
            }
        },
    )

    # 13. If another request updated the record first, return a conflict.
    if result.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already punched out by another request",
        )

    # 14. Fetch the updated record.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    # 15. Return serialized data with EpochMillis timestamps.
    #     serialize_doc must exclude MongoDB's internal _id.
    return serialize_doc(updated)
#-----------------------------------------------------------------------------#
#   PATCH /attendance/{emp_code}/{date}

#------------------------------------------------------------------------------#

class RegularizeRequest(BaseModel):
    by:str
    reason:str

    status: Optional[str] = None
    punch_in: Optional[int] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )
    punch_out: Optional[int] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )
    


ALLOWED_STATUSES = {
    "PRESENT",
    "WFH",
    "ON_DUTY",
    "ABSENT",
    "LEAVE",
}



@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: date,
    body: RegularizeRequest,
):
    # 1. Validate employee and attendance record using emp_code and date.
    employee = employee_or_404(emp_code)

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date.isoformat(),
    })

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    supplied = body.model_fields_set

    old_status = record.get("status", "PRESENT")

    # 2. Convert stored MongoDB timestamps to UTC-aware datetimes.
    #    Naive BSON datetimes from PyMongo represent UTC.
    def stored_datetime_to_utc(value):
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)

        return value.replace(microsecond=0)

    old_in = stored_datetime_to_utc(record.get("punch_in"))
    old_out = stored_datetime_to_utc(record.get("punch_out"))

    # 3. Determine the final attendance status.
    status = (
        body.status.strip().upper()
        if "status" in supplied and body.status is not None
        else old_status
    )

    if status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Allowed values: {sorted(ALLOWED_STATUSES)}",
        )

    # 4. Convert supplied EpochMillis timestamps to UTC datetimes.
    #    Omitted fields remain unchanged.
    punch_in = old_in
    punch_out = old_out

    if "punch_in" in supplied and body.punch_in is not None:
        punch_in = epoch_millis_to_datetime(body.punch_in)

    if "punch_out" in supplied and body.punch_out is not None:
        punch_out = epoch_millis_to_datetime(body.punch_out)

    # 5. ABSENT and LEAVE records must have no punch times.
    if status in {"ABSENT", "LEAVE"}:
        if (
            ("punch_in" in supplied and body.punch_in is not None)
            or ("punch_out" in supplied and body.punch_out is not None)
        ):
            raise HTTPException(
                status_code=422,
                detail="Cannot supply punch times when status is ABSENT or LEAVE",
            )

        punch_in = None
        punch_out = None

    else:
        # 6. Validate the punch-in and attendance date.
        if punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="A presence status requires a punch_in time",
            )

        punch_in = punch_in.replace(microsecond=0)

        if punch_out is not None:
            punch_out = punch_out.replace(microsecond=0)

        # Convert punch-in to IST for attendance-date and shift calculations.
        punch_in_ist = punch_in.astimezone(IST)

        scheduled_start, _ = get_shift_datetimes(
            punch_in_ist,
            employee,
        )

        if scheduled_start.date() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in does not belong to the requested attendance date",
            )

        # 7. Validate punch-out if it exists.
        if punch_out is not None:
            elapsed_seconds = (
                punch_out - punch_in
            ).total_seconds()

            if elapsed_seconds <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be later than punch_in",
                )

            if elapsed_seconds > 24 * 60 * 60:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out cannot be more than 24 hours after punch_in",
                )

    # 8. Build a history changes object containing only actual changes.
    changes = {}

    if status != old_status:
        changes["status"] = {
            "from": old_status,
            "to": status,
        }

    if punch_in != old_in:
        changes["punch_in"] = {
            "from": old_in,
            "to": punch_in,
        }

    if punch_out != old_out:
        changes["punch_out"] = {
            "from": old_out,
            "to": punch_out,
        }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Request does not change anything",
        )

    # 9. Recalculate derived attendance fields.
    if punch_in is None:
        late_minutes = 0
        work_hours = None
        overtime_minutes = 0
        half_day = False

    else:
        punch_in_ist = punch_in.astimezone(IST)

        late_minutes = compute_late_minutes(
            punch_in_ist,
            employee["shift_start"],
            employee["shift_end"],
        )

        if punch_out is None:
            work_hours = None
            overtime_minutes = 0
            half_day = False

        else:
            work_hours = compute_work_hours(
                punch_in,
                punch_out,
            )

            half_day = compute_half_day(work_hours)

            _, scheduled_end = get_shift_datetimes(
                punch_in_ist,
                employee,
            )

            punch_out_ist = punch_out.astimezone(IST)

            overtime_seconds = (
                punch_out_ist - scheduled_end
            ).total_seconds()

            overtime_minutes = (
                int(overtime_seconds // 60)
                if overtime_seconds >= 30 * 60
                else 0
            )

    # 10. Create the manual-correction history entry.
    #      Add `by` and `reason` to RegularizeRequest and populate them
    #      from the API contract/authentication as required.
    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.by.strip(),
        "reason": body.reason.strip(),
        "changes": changes,
    }

    original_history = record.get("history", [])

    # 11. Build a concurrency filter using the original stored values.
    #     Do not overwrite another request's changes.
    query = {
        "_id": record["_id"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "history": original_history,
    }

    if "status" in record:
        query["status"] = record["status"]
    else:
        query["status"] = {"$exists": False}

    # 12. Atomically update the attendance record and append one history entry.
    result = db.attendance_logs.update_one(
        query,
        {
            "$set": {
                "status": status,
                "punch_in": punch_in,
                "punch_out": punch_out,
                "late_minutes": late_minutes,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
                "updated_at": datetime.now(UTC).replace(microsecond=0),
            },
            "$push": {
                "history": history_entry,
            },
        },
    )

    if result.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    # 13. Fetch and return the updated record.
    #      serialize_doc must convert all nested datetimes to EpochMillis
    #      and exclude MongoDB's internal _id.
    updated = db.attendance_logs.find_one({
        "_id": record["_id"]
    })

    return serialize_doc(updated)

#---------------------------------------------------------------#

#GET   /analytics/employees/{emp_code}/monthly

#----------------------------------------------------------------#

def round_half_up(value, places=2):
    """Round using the required ROUND_HALF_UP rule."""
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


def count_weekdays(start_date: date, end_date: date) -> int:
    """Count Monday-Friday dates, inclusive of both endpoints."""
    if start_date > end_date:
        return 0

    total = 0
    current_date = start_date

    while current_date <= end_date:
        if current_date.weekday() < 5:
            total += 1

        current_date += timedelta(days=1)

    return total


def parse_joined_on(value):
    """
    Employee schema: joined_on is YYYY-MM-DD.
    BSON datetime values are also handled defensively.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        # MongoDB BSON datetimes are normally returned as naive UTC.
        # Convert to IST before extracting the calendar date.
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)

        return value.astimezone(IST).date()

    if isinstance(value, date):
        return value

    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise HTTPException(
                status_code=500,
                detail="Invalid joined_on value in employee record",
            )

    raise HTTPException(
        status_code=500,
        detail="Invalid joined_on value in employee record",
    )


@app.get("/analytics/employees/{emp_code}/monthly")
def monthly_employee_analytics(
    emp_code: str,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    # 1. Find employee using the business identifier, not MongoDB _id.
    employee = employee_or_404(emp_code)

    # 2. Determine the requested month using calendar dates.
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])

    today = datetime.now(IST).date()

    # Do not count future dates as absences.
    period_end = min(month_end, today)

    # 3. Determine the employee's effective reporting start date.
    joined_on = parse_joined_on(employee.get("joined_on"))

    if joined_on is not None and joined_on > month_end:
        raise HTTPException(
            status_code=422,
            detail="Employee joined after the requested month",
        )

    effective_start = (
        max(month_start, joined_on)
        if joined_on is not None
        else month_start
    )

    # 4. Count Monday-Friday working days.
    working_days = (
        count_weekdays(effective_start, period_end)
        if effective_start <= period_end
        else 0
    )

    # 5. Fetch attendance records for this employee and month.
    # The date field is expected to be stored as YYYY-MM-DD.
    records = db.attendance_logs.find(
        {
            "emp_code": emp_code,
            "date": {
                "$gte": month_start.isoformat(),
                "$lte": month_end.isoformat(),
            },
        },
        {
            "_id": 0,
            "emp_code": 1,
            "date": 1,
            "status": 1,
            "half_day": 1,
            "punch_out": 1,
            "work_hours": 1,
            "late_minutes": 1,
            "overtime_minutes": 1,
        },
    )

    # 6. Initialize metrics.
    present_days = 0.0
    half_days = 0
    leave_days = 0
    recorded_absent_days = 0

    total_work_hours = 0.0
    late_days = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    recorded_dates = set()

    # 7. Process stored attendance records.
    for record in records:
        record_date_string = record.get("date")

        try:
            record_date = date.fromisoformat(record_date_string)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=500,
                detail="Invalid attendance date in database",
            )

        # Only include dates in the employee's reporting period.
        if not effective_start <= record_date <= period_end:
            continue

        recorded_dates.add(record_date)

        status = record.get("status", "PRESENT")
        is_weekday = record_date.weekday() < 5

        # Count attendance statuses on weekdays only.
        if is_weekday:
            if status in {"PRESENT", "WFH", "ON_DUTY"}:
                if record.get("half_day", False):
                    present_days += 0.5
                    half_days += 1
                else:
                    present_days += 1.0

            elif status == "LEAVE":
                leave_days += 1

            elif status == "ABSENT":
                recorded_absent_days += 1

        # Work hours count only when punch-out is recorded.
        # Weekend work still contributes to work totals.
        if record.get("punch_out") is not None:
            total_work_hours += float(
                record.get("work_hours") or 0
            )

        # Late arrival metrics.
        late_minutes = int(record.get("late_minutes") or 0)

        if late_minutes > 0:
            late_days += 1
            total_late_minutes += late_minutes

        # Overtime metrics.
        total_overtime_minutes += int(
            record.get("overtime_minutes") or 0
        )

    # 8. Count weekdays without any attendance record as absent.
    missing_days = 0
    current_date = effective_start

    while current_date <= period_end:
        if (
            current_date.weekday() < 5
            and current_date not in recorded_dates
        ):
            missing_days += 1

        current_date += timedelta(days=1)

    absent_days = recorded_absent_days + missing_days

    # 9. Calculate attendance rate.
    # This is a ratio from 0.0 to 1.0, not a percentage.
    attendance_rate = (
        round_half_up(
            present_days / working_days,
            4,
        )
        if working_days > 0
        else 0.0
    )

    # 10. Round work hours as required.
    total_work_hours = round_half_up(total_work_hours, 2)

    # 11. Return the monthly report.
    return {
        "emp_code": emp_code,
        "employee_name": employee.get("name"),
        "year": year,
        "month": month,
        "period": {
            "start_date": month_start.isoformat(),
            "end_date": month_end.isoformat(),
            "calculated_through": (
                period_end.isoformat()
                if period_end >= month_start
                else None
            ),
        },
        "attendance": {
            "working_days": working_days,
            "present_days": present_days,
            "absent_days": absent_days,
            "leave_days": leave_days,
            "half_days": half_days,
            "attendance_rate": attendance_rate,
        },
        "work": {
            "total_work_hours": total_work_hours,
            "late_days": late_days,
            "total_late_minutes": total_late_minutes,
            "total_overtime_minutes": total_overtime_minutes,
        },
    }



#------------------------------------------------------------#

#   GET   /analytics/departments/summary

#--------------------------------------------------------------#
import calendar
PRESENT_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]

def get_month_dates(month: str) -> tuple[date, date]:
    """
    Parse YYYY-MM and return the first and last date of that month.
    """
    if not isinstance(month, str) or not re.fullmatch(
        r"\d{4}-(0[1-9]|1[0-2])", month
    ):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format, e.g. 2026-10",
        )

    year, month_number = map(int, month.split("-"))

    if not 2000 <= year <= 2100:
        raise HTTPException(
            status_code=422,
            detail="year must be between 2000 and 2100",
        )

    month_start = date(year, month_number, 1)
    month_end = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1],
    )

    return month_start, month_end


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        description="Reporting month in YYYY-MM format",
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: str | None = Query(None),
):
    # ---------------------------------------------------------
    # 1. Parse reporting month and determine effective end date.
    # ---------------------------------------------------------
    month_start, month_end = get_month_dates(month)

    today = datetime.now(IST).date()
    effective_end = min(month_end, today)

    # MongoDB stores these fields as YYYY-MM-DD strings.
    month_start_str = month_start.isoformat()
    month_end_str = month_end.isoformat()
    effective_end_str = effective_end.isoformat()

    # ---------------------------------------------------------
    # 2. Match employees who joined on or before month-end.
    #    Employees without attendance logs remain included.
    # ---------------------------------------------------------
    employee_match = {
        "joined_on": {"$lte": month_end_str}
    }

    if department is not None:
        employee_match["department"] = department

    # ---------------------------------------------------------
    # 3. Aggregate employees and their attendance records.
    # ---------------------------------------------------------
    pipeline = [
        {
            "$match": employee_match
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "code": "$emp_code"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$code",
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start_str,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$date",
                                            effective_end_str,
                                        ]
                                    },
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },

        # Determine whether each attendance record falls on
        # Monday-Friday. MongoDB dayOfWeek: Sunday=1, Monday=2,
        # ..., Friday=6, Saturday=7.
        {
            "$addFields": {
                "is_weekday": {
                    "$cond": [
                        {
                            "$ne": [
                                {"$ifNull": ["$logs.date", None]},
                                None,
                            ]
                        },
                        {
                            "$and": [
                                {
                                    "$gte": [
                                        {
                                            "$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$logs.date",
                                                    "format": "%Y-%m-%d",
                                                }
                                            }
                                        },
                                        2,
                                    ]
                                },
                                {
                                    "$lte": [
                                        {
                                            "$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$logs.date",
                                                    "format": "%Y-%m-%d",
                                                }
                                            }
                                        },
                                        6,
                                    ]
                                },
                            ]
                        },
                        False,
                    ]
                }
            }
        },

        # -----------------------------------------------------
        # 4. Group by department.
        # -----------------------------------------------------
        {
            "$group": {
                "_id": "$department",

                # Count distinct employees, including those
                # who have no attendance records.
                "employee_codes": {
                    "$addToSet": "$emp_code"
                },

                # Count weekday presence. Half-days contribute 0.5.
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    {
                                        "$in": [
                                            "$logs.status",
                                            PRESENT_STATUSES,
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            "$logs.half_day",
                                            True,
                                        ]
                                    },
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },

                # Work hours: average of records with non-null
                # work_hours and a presence status.
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            PRESENT_STATUSES,
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$logs.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$logs.work_hours",
                            0,
                        ]
                    }
                },

                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            PRESENT_STATUSES,
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$logs.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Count late attendance records on weekdays.
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    {
                                        "$gt": [
                                            {
                                                "$ifNull": [
                                                    "$logs.late_minutes",
                                                    0,
                                                ]
                                            },
                                            0,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Total late minutes, including weekend records.
                "total_late_minutes": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {
                                        "$ifNull": [
                                            "$logs.late_minutes",
                                            0,
                                        ]
                                    },
                                    0,
                                ]
                            },
                            {
                                "$ifNull": [
                                    "$logs.late_minutes",
                                    0,
                                ]
                            },
                            0,
                        ]
                    }
                },

                # Count weekday leave records.
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    {
                                        "$eq": [
                                            "$logs.status",
                                            "LEAVE",
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Count weekday on-duty records.
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    {
                                        "$eq": [
                                            "$logs.status",
                                            "ON_DUTY",
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },

        # -----------------------------------------------------
        # 5. Shape output and calculate employee headcount.
        # -----------------------------------------------------
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {
                    "$size": "$employee_codes"
                },
                "present_days": 1,
                "work_hours_sum": 1,
                "work_hours_count": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {
            "$match": {
                "headcount": {"$gt": 0}
            }
        },
        {
            "$sort": {
                "department": 1
            }
        },
    ]

    results = list(db.employees.aggregate(pipeline))

    # ---------------------------------------------------------
    # 6. Format the response.
    # ---------------------------------------------------------
    departments = []

    for item in results:
        hours_count = item.pop("work_hours_count")
        hours_sum = item.pop("work_hours_sum")

        item["present_days"] = round_half_up(
            item["present_days"],
            1,
        )

        item["avg_work_hours"] = (
            round_half_up(hours_sum / hours_count, 2)
            if hours_count > 0
            else None
        )

        item["total_late_minutes"] = int(
            item["total_late_minutes"]
        )

        departments.append(item)

    return {
        "month": month,
        "departments": departments,
    }

#--------------------------------------------------------------#
#   GET   /analytics/leaderboard/late
#--------------------------------------------------------------#

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        description="Reporting month in YYYY-MM format",
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(10, ge=1, le=50),
    department: str | None = Query(None),
):
    # 1. Get the first and last dates of the requested month.
    # get_month_dates() returns Python date objects.
    month_start, month_end = get_month_dates(month)

    # 2. Use the current IST date as the reporting cutoff.
    today = datetime.now(IST).date()
    effective_end = min(month_end, today)

    # 3. Convert dates to strings because MongoDB stores attendance
    #    dates as YYYY-MM-DD strings.
    month_start_str = month_start.isoformat()
    effective_end_str = effective_end.isoformat()

    # 4. Match attendance records with positive late minutes.
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start_str,
                    "$lte": effective_end_str,
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {
            "$unwind": "$employee"
        },
    ]

    # 5. Apply the optional department filter.
    if department is not None:
        pipeline.append({
            "$match": {
                "employee.department": department
            }
        })

    # 6. Group attendance records by employee and calculate
    #    the total late minutes for the requested month.
    pipeline.extend([
        {
            "$group": {
                "_id": "$emp_code",
                "name": {"$first": "$employee.name"},
                "department": {"$first": "$employee.department"},
                "total_late_minutes": {
                    "$sum": "$late_minutes"
                },
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
    ])

    employees = list(db.attendance_logs.aggregate(pipeline))

    # 7. Assign dense ranks.
    #    Example: 60, 60, 45 -> ranks 1, 1, 2.
    leaderboard = []
    previous_total = None
    current_rank = 0

    for employee in employees:
        total = employee["total_late_minutes"]

        if total != previous_total:
            current_rank += 1
            previous_total = total

        # Include only the requested number of distinct ranks.
        if current_rank > limit:
            break

        leaderboard.append({
            "rank": current_rank,
            "emp_code": employee["_id"],
            "name": employee.get("name"),
            "department": employee.get("department"),
            "total_late_minutes": total,
        })

    # 8. Return the leaderboard.
    return {
        "month": month,
        "limit": limit,
        "leaderboard": leaderboard,
    }