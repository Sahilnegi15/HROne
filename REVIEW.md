# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.

 
## Helpers
 
### 1. `compute_late_minutes()`
 
**What is wrong:**
The original code used only `shift_start`, so it gave wrong results for overnight shifts because it did not consider `shift_end`. It also did not truncate seconds, and it used `int(minutes) > 10`, so 09:40:01 returned 0 instead of 10.
 
**How I noticed it:**
For a 22:00 - 06:00 shift, punch in at 04:00 IST. The shift started at 22:00 the previous day, so the employee is not late, but the original code measured from 22:00 on the same day.
For a 09:30 shift, a punch-in at 09:40:01 must return 10.
 
**How I fixed it:**
Used both `shift_start` and `shift_end` to find the correct shift date (previous day for early-morning punches on an overnight shift), converted the punch to IST, truncated seconds, and returned the floored whole minutes since `shift_start` only when the delay is strictly more than 10 minutes.
 
### 2. `compute_overtime()`
 
**What is wrong:**
- The original code does not enforce the required 30 minute minimum rule.
- The shift end calculation can be wrong if the supplied attendance date is not the date on which the shift started.
**How I noticed it:**
- For a shift ending at 18:00, test punch-outs at 18:29 and 18:30. Both results should follow the explicit overtime threshold.
- For a 22:00-06:00 shift, supply the shift-start date and punch out at 06:30 the next day.
**How I fixed it:**
- Calculate the correct shift-end datetime, including the following day for overnight shifts, and return overtime only when at least 30 complete minutes have elapsed.
- Treat `date_str` as the shift-start date and add one day to the shift-end date for an overnight shift.
### 3. `compute_work_hours()`
 
**What is wrong:**
The original code used Python `round()` on a float, which is not half-up rounding, and it did not truncate seconds first.
 
**How I noticed it:**
8 hours 7 minutes 30 seconds must round half-up to 8.13.
 
**How I fixed it:**
Truncated both instants to whole seconds and used `Decimal` with `ROUND_HALF_UP` to 2 decimals.
 
### Additional functions added
 
The API sends datetimes in the format of Epoch milliseconds and PyMongo stores them in BSON format, so these two functions were created to convert between them and solve this issue.
 
1. `epoch_millis_to_datetime()`:
   converts Epoch milliseconds into a timezone-aware UTC datetime, ready to be stored by PyMongo as a BSON Date.
2. `datetime_to_epoch_millis()`:
   converts a timezone-aware datetime back into an integer representing Epoch milliseconds.
---
 
## Endpoints
 
### Models
 
1. There is a missing field in the `EmployeeIn` model, which is `created_at`.
2. There is a limit given for Epoch milliseconds, so in the `PunchInIn` model the limit is not given.
### 1. `POST /employees` (status 201)
 
The date is directly inserted into the `created_at` field without converting the epoch value to BSON format.
 
### 2. `GET /employees`
 
- `skip` is not calculated properly.
- Pagination validation is missing.
- Incorrect total count: this counts every employee, even when the request filters by department.
**Fix:**
- `skip = (page - 1) * page_size`
- `total = count_documents(q)`
- validate `page` and `page_size` (max 100)
- sort by `emp_code`
### 3. `POST /attendance/punch-in` (status 201)
 
- Provides only `shift_start` to `compute_late_minutes`.
- Does not convert epoch to a timezone-aware datetime.
### 4. `GET /attendance`
 
- Pagination parameters are not validated.
- Calculates total incorrectly for efficient pagination.
- Sorting is done in Python after retrieving all records.
- Loads every attendance record into Python.