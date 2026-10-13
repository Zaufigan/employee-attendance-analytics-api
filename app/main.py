"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code"
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_code_date"
    )
# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in, shift_start):
    punch_in = punch_in.astimezone(IST).replace(microsecond=0)

    h, m = map(int, shift_start.split(":"))
    shift_start_time = punch_in.replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    # Overnight shift: early-morning punch-in belongs to
    # the previous day's shift.
    if punch_in.hour * 60 + punch_in.minute < h * 60 + m:
        shift_start_time -= timedelta(days=1)

    minutes = int(
        (punch_in - shift_start_time).total_seconds() // 60
    )
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round_half_up(
        (punch_out - punch_in).total_seconds() / 3600
    )


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    punch_in_date = datetime.fromisoformat(date_str).date()

    shift_end_time = datetime(
        punch_in_date.year,
        punch_in_date.month,
        punch_in_date.day,
        h, m,
        tzinfo=IST
    )

    # If shift ends at or before its start, it ends next day.
    # The shift start is not available in this helper, so callers
    # should pass a correctly resolved shift-end for overnight shifts.
    if punch_out.astimezone(IST) < shift_end_time:
        return 0

    minutes = int(
        (punch_out.astimezone(IST) - shift_end_time).total_seconds() // 60
    )
    return minutes if minutes >= 30 else 0
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


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None    


class RegularizationIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


from pymongo.errors import DuplicateKeyError
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now()

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status"
        )

    if body.punched_at is None:
        ts = datetime.now(IST).replace(microsecond=0)
    else:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or not 100000000000 <= body.punched_at <= 4102444800000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be a valid epoch timestamp in milliseconds"
            )

        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        ).astimezone(IST).replace(microsecond=0)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    is_overnight = (end_hour, end_minute) <= (start_hour, start_minute)

    attendance_day = ts.date()

    if is_overnight and (ts.hour, ts.minute) < (end_hour, end_minute):
        attendance_day -= timedelta(days=1)

    d = attendance_day.isoformat()

    # MongoDB stores timestamps in UTC.
    punch_in_utc = ts.astimezone(timezone.utc).replace(tzinfo=None)

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except Exception as exc:
        # Duplicate natural key should return 409.
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            raise HTTPException(
                status_code=409,
                detail="Already punched in for this date"
            )
        raise

    doc["id"] = str(result.inserted_id)
    doc["punch_in"] = ts.isoformat()
    doc.pop("_id", None)
    return doc

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    q = {}

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

    total = db.attendance_logs.count_documents(q)

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort("date", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for d in docs:
        for field in ("punch_in", "punch_out"):
            d[field] = to_epoch_ms(d.get(field))

        # Legacy records may not contain these fields.
        d.setdefault("history", [])
        d.setdefault("half_day", False)

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out


from decimal import Decimal, ROUND_HALF_UP

def to_epoch_ms(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def round_half_up(value, places="0.01"):
    return float(
        Decimal(str(value)).quantize(
            Decimal(places), rounding=ROUND_HALF_UP
        )
    )


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    from pymongo import ReturnDocument

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or not 100000000000 <= body.punched_at <= 4102444800000
        ):
            raise HTTPException(422, "Invalid punched_at timestamp")

        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    ts_ist = ts.astimezone(IST)
    punch_in_utc = {
        "$ne": None
    }

    # Find the most recent open attendance record at or before punch-out.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts.replace(tzinfo=None)},
            "punch_out": None,
        },
        sort=[("punch_in", -1)]
    )

    if not record:
        # Distinguish an already-closed record from no punch-in.
        closed = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$ne": None, "$lte": ts.replace(tzinfo=None)},
            },
            sort=[("punch_in", -1)]
        )
        if closed:
            raise HTTPException(409, "Already punched out")
        raise HTTPException(404, "No punch-in found")

    punch_in_naive = record["punch_in"]
    punch_in_aware = punch_in_naive.replace(tzinfo=timezone.utc)

    elapsed = (ts - punch_in_aware).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(
            422,
            "Punch-out must be after punch-in and within 24 hours"
        )

    work_hours = round_half_up(elapsed / 3600)

    shift_end_hour, shift_end_minute = map(
        int, emp["shift_end"].split(":")
    )
    shift_start_hour, shift_start_minute = map(
        int, emp["shift_start"].split(":")
    )

    punch_in_ist = punch_in_aware.astimezone(IST)
    shift_end = punch_in_ist.replace(
        hour=shift_end_hour,
        minute=shift_end_minute,
        second=0,
        microsecond=0
    )

    # Overnight shift ends on the following calendar day.
    if (shift_end_hour, shift_end_minute) <= (
        shift_start_hour, shift_start_minute
    ):
        shift_end += timedelta(days=1)

    overtime_elapsed = int((ts.astimezone(IST) - shift_end).total_seconds() // 60)
    overtime_minutes = overtime_elapsed if overtime_elapsed >= 30 else 0

    half_day = work_hours < 4.50

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts.replace(tzinfo=None),
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER
    )

    if updated is None:
        raise HTTPException(409, "Already punched out")

    updated.pop("_id", None)
    updated["punch_in"] = to_epoch_ms(punch_in_aware)
    updated["punch_out"] = to_epoch_ms(ts)
    return updated
#PATCH /attendance/{emp_code}/{date}

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizationIn
):
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    from datetime import date as date_type

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    try:
        date_type.fromisoformat(date)
        if len(date) != 10:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")

    allowed_statuses = {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }

    if body.status is not None and body.status not in allowed_statuses:
        raise HTTPException(422, "Invalid status")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if not record:
        raise HTTPException(404, "Attendance record not found")

    new_status = body.status or record.get("status")
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    def parse_epoch(value, field):
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 100000000000 <= value <= 4102444800000
        ):
            raise HTTPException(
                422, f"{field} must be epoch milliseconds"
            )
        return datetime.fromtimestamp(
            value / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422, "ABSENT/LEAVE cannot include punch times"
            )
        new_in = None
        new_out = None
    else:
        if body.punch_in is not None:
            new_in = parse_epoch(body.punch_in, "punch_in")
        else:
            new_in = (
                old_in.replace(tzinfo=timezone.utc)
                if old_in is not None and old_in.tzinfo is None
                else old_in
            )

        if body.punch_out is not None:
            new_out = parse_epoch(body.punch_out, "punch_out")
        else:
            new_out = (
                old_out.replace(tzinfo=timezone.utc)
                if old_out is not None and old_out.tzinfo is None
                else old_out
            )

        if new_in is None:
            raise HTTPException(
                422, "Presence status requires punch_in"
            )

        if new_in.astimezone(IST).date().isoformat() != date:
            raise HTTPException(
                422, "punch_in must belong to the attendance date"
            )

        if new_out is not None:
            seconds = (new_out - new_in).total_seconds()
            if seconds <= 0 or seconds > 24 * 3600:
                raise HTTPException(
                    422, "punch_out must be after punch_in and within 24 hours"
                )

    def db_datetime(value):
        if value is None:
            return None
        return value.astimezone(timezone.utc).replace(
            tzinfo=None, microsecond=0
        )

    changes = {}

    if new_status != record.get("status"):
        changes["status"] = new_status

    if db_datetime(new_in) != old_in:
        changes["punch_in"] = db_datetime(new_in)

    if db_datetime(new_out) != old_out:
        changes["punch_out"] = db_datetime(new_out)

    if not changes:
        raise HTTPException(422, "No changes provided")

    if new_status in {"ABSENT", "LEAVE"}:
        derived = {
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    else:
        in_ist = new_in.astimezone(IST)
        late = compute_late_minutes(in_ist, emp["shift_start"])

        work_hours = None
        overtime = 0
        half_day = False

        if new_out is not None:
            seconds = (new_out - new_in).total_seconds()
            work_hours = round_half_up(seconds / 3600)
            half_day = work_hours < 4.50

            sh, sm = map(int, emp["shift_start"].split(":"))
            eh, em = map(int, emp["shift_end"].split(":"))

            shift_start = in_ist.replace(
                hour=sh, minute=sm, second=0, microsecond=0
            )
            shift_end = in_ist.replace(
                hour=eh, minute=em, second=0, microsecond=0
            )

            if shift_end <= shift_start:
                shift_end += timedelta(days=1)

            extra = int(
                (new_out.astimezone(IST) - shift_end).total_seconds() // 60
            )
            if extra >= 30:
                overtime = extra

        derived = {
            "work_hours": work_hours,
            "late_minutes": late,
            "overtime_minutes": overtime,
            "half_day": half_day,
        }

    update_fields = {
        "status": new_status,
        "punch_in": db_datetime(new_in),
        "punch_out": db_datetime(new_out),
        **derived,
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }

    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": emp_code, "date": date},
        {
            "$set": update_fields,
            "$push": {
                "history": {
    "at": int(datetime.now(timezone.utc).timestamp() * 1000),
    "by": body.regularized_by,
    "reason": body.reason,
    "changes": {
        key: {
            "from": (
                to_epoch_ms(old_in)
                if key == "punch_in" and old_in is not None
                else to_epoch_ms(old_out)
                if key == "punch_out" and old_out is not None
                else record.get(key)
            ),
            "to": (
                to_epoch_ms(new_in)
                if key == "punch_in"
                else to_epoch_ms(new_out)
                if key == "punch_out"
                else update_fields.get(key)
            ),
        }
        for key in changes
    },
}
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(404, "Attendance record not found")

    updated.pop("_id", None)
    updated["punch_in"] = to_epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = to_epoch_ms(updated.get("punch_out"))
    return updated
#   GET   /analytics/employees/{emp_code}/monthly

import calendar
from decimal import Decimal, ROUND_HALF_UP



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    try:
        year, mon = map(int, month.split("-"))
        if not 1 <= mon <= 12:
            raise ValueError()
        last_day = calendar.monthrange(year, mon)[1]
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    start_date = f"{year:04d}-{mon:02d}-01"
    end_date = f"{year:04d}-{mon:02d}-{last_day:02d}"

    joined_on = emp["joined_on"]
    effective_start = max(start_date, joined_on)

    working_days = 0
    if effective_start <= end_date:
        current = datetime.fromisoformat(effective_start).date()
        last = datetime.fromisoformat(end_date).date()
        while current <= last:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": start_date, "$lte": end_date}
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {
                "$cond": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"$cond": [
                        {"$eq": ["$half_day", True]}, 0.5, 1
                    ]},
                    0
                ]
            }},
            "leave_days": {"$sum": {
                "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
            }},
            "late_count": {"$sum": {
                "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
            }},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"}
        }}
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    stats = result[0] if result else {}

    # Present days count only Monday-Friday.
    # Weekend logs still contribute to late/overtime totals.
    weekday_pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": start_date, "$lte": end_date},
            "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]}
        }},
        {"$addFields": {
            "weekday": {
                "$dayOfWeek": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "timezone": "Asia/Kolkata"
                    }
                }
            }
        }},
        {"$match": {"weekday": {"$gte": 2, "$lte": 6}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {
                "$cond": [
                    {"$eq": ["$half_day", True]}, 0.5, 1
                ]
            }}
        }}
    ]

    weekday_result = list(db.attendance_logs.aggregate(weekday_pipeline))
    present_days = weekday_result[0]["present_days"] if weekday_result else 0

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal("100") /
             Decimal(working_days)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct
    }
#   GET   /analytics/departments/summary
@app.get("/analytics/departments/summary")
def department_summary():
    pipeline = [
        {
            "$group": {
                "_id": "$department",
                "employee_count": {"$sum": 1}
            }
        },
        {
            "$sort": {"_id": 1}
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "employee_count": 1
            }
        }
    ]

    result = list(db.employees.aggregate(pipeline))

    return {"items": result}
#   GET   /analytics/leaderboard/late

@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard():
    pipeline = [
        {
            "$group": {
                "_id": "$emp_code",
                "late_days": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "late_days": 1,
                "total_late_minutes": 1
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    return {"items": result}
#   GET   /analytics/departments/{department}/trend

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    from datetime import date as date_type, timedelta
    from decimal import Decimal, ROUND_HALF_UP

    try:
        start = date_type.fromisoformat(from_date)
        end = date_type.fromisoformat(to_date)
        if start.isoformat() != from_date or end.isoformat() != to_date:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD")

    if end < start:
        raise HTTPException(422, "'to' must be on or after 'from'")

    days = (end - start).days + 1
    if days > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")

    employees = list(db.employees.find(
        {"department": department},
        {"_id": 0, "emp_code": 1, "joined_on": 1}
    ))

    if not employees:
        raise HTTPException(404, "Department not found")

    employee_codes = [e["emp_code"] for e in employees]
    logs = list(db.attendance_logs.find(
        {
            "emp_code": {"$in": employee_codes},
            "date": {"$gte": from_date, "$lte": to_date}
        },
        {
            "_id": 0, "emp_code": 1, "date": 1,
            "status": 1, "half_day": 1, "late_minutes": 1
        }
    ))

    by_date = {}
    for log in logs:
        by_date.setdefault(log["date"], []).append(log)

    result = []
    rates = []
    current = start

    while current <= end:
        day = current.isoformat()
        working = current.weekday() < 5

        active = sum(
            1 for e in employees
            if e.get("joined_on", "9999-12-31") <= day
        )

        daily_logs = by_date.get(day, [])
        present = sum(
            0.5 if log.get("half_day") else 1
            for log in daily_logs
            if log.get("status") in {"PRESENT", "WFH", "ON_DUTY"}
        )
        late = sum(
            1 for log in daily_logs
            if log.get("late_minutes", 0) > 0
        )

        rate = None
        if working and active > 0:
            rate = float(
                (Decimal(str(present * 100 / active))).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            )

        rates.append(rate)
        recent = [r for r in rates[-7:] if r is not None]
        moving_avg = (
            float(Decimal(str(sum(recent) / len(recent))).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ))
            if recent else None
        )

        result.append({
            "date": day,
            "is_working_day": working,
            "headcount": active,
            "present_count": present,
            "late_count": late,
            "attendance_rate": rate,
            "moving_avg_7d": moving_avg
        })

        current += timedelta(days=1)

    return {"items": result}
#   GET   /admin/explain/{endpoint}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    allowed_endpoints = {
        "employees": db.employees,
        "attendance": db.attendance_logs,
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=404,
            detail="Unknown endpoint. Use employees or attendance."
        )

    collection = allowed_endpoints[endpoint]

    if endpoint == "employees":
        query = {}
        sort_order = [("emp_code", 1)]
    else:
        query = {}
        sort_order = [("date", -1), ("emp_code", 1)]

    explanation = collection.find(query).sort(sort_order).explain()

    return {
        "endpoint": endpoint,
        "query_plan": explanation.get("queryPlanner", {}).get("winningPlan", {}),
        "execution_stats": explanation.get("executionStats", {})
    }
# --------------------------------------------------------------------------- #
