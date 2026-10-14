# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.



## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.


# Candidate Solution

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Readiness probe |
| POST | `/employees` | Create an employee (`emp_code` is unique) |
| GET | `/employees` | List employees, filter by department, paginated |
| POST | `/attendance/punch-in` | Record a punch-in (one record per employee per day) |
| POST | `/attendance/punch-out` | Close the latest open record; computes work hours, overtime and half day |
| GET | `/attendance` | List records with filters (`emp_code`, `date_from`, `date_to`, `status`), paginated |
| PATCH | `/attendance/{emp_code}/{date}` | Manually correct a record; recomputes derived fields and appends one history entry |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly summary for one employee |
| GET | `/analytics/departments/summary` | Department summary for a month (MongoDB aggregation) |
| GET | `/analytics/leaderboard/late` | Late-comers leaderboard for a month |

## Key rules implemented

- Employees are identified by `emp_code`, never by MongoDB `_id`.
- **Instants** are integer epoch milliseconds in the API and BSON datetimes (UTC) in MongoDB. **Calendar dates** (`YYYY-MM-DD`) and **shift times** (`HH:MM`, IST) are strings.
- **Time zone:** shifts and attendance dates use IST (UTC+05:30). Overnight shifts (`shift_end <= shift_start`) are handled: an early-morning punch belongs to the shift that started the previous day.
- **Seconds:** instants are truncated to whole seconds before any calculation.
- **Late:** more than 10 minutes after `shift_start`; the value is the whole minutes since `shift_start`.
- **Overtime:** whole minutes after `shift_end`, counted only when at least 30.
- **Work hours:** seconds / 3600 rounded to 2 decimals, half-up.
- **Half day:** rounded work hours below 4.50; it counts as 0.5 of a present day.
- **Presence:** `PRESENT`, `WFH` and `ON_DUTY` count as present; `ABSENT` and `LEAVE` do not.
- **Working days:** Monday to Friday, counted from `joined_on` for mid-month joiners, with no holiday calendar.
- **Concurrency:** punch-out and PATCH use a conditional `update_one`, so simultaneous requests cannot both succeed. The loser gets 409.
- **Analytics** read the stored derived values and do not recompute them from punch times.

## Indexes

Indexes are created at startup and the creation is idempotent. The main ones are a unique index on `employees.emp_code`, a unique `(emp_code, date)` index on `attendance_logs`, `(date, emp_code)` for the list and sort, and `(emp_code, punch_in)` for punch-out.

## Project files

- `app/main.py`: the API
- `requirements.txt`: dependencies
- `REVIEW.md`: defects found in the starter code
- `DECISIONS.md`: design decisions
- `openapi.yaml`, `DATA_MODEL.md`: the contract and the data model
- `sample_seed.py`, `sample_data/`: sample data loader