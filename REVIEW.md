# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.



## Helpers :---
compute_late_minutes() :-
What is wrong:-
The original code used only shift_start, so it gave wrong results for overnight shifts because it did not consider shift_end. It also did not truncate seconds, and it used int(minutes) > 10, so 09:40:01 returned 0 instead of 10.
How I noticed it :-
For a 22:00 - 06:00 shift, punch in at 04:00 IST. The shift started at 22:00 the previous day, so the employee is not late, but the original code measured from 22:00 on the same day. For a 09:30 shift, a punch-in at 09:40:01 must return 10.
How I fixed it :-
Used both shift_start and shift_end to find the correct shift date (previous day for early-morning punches on an overnight shift), converted the punch to IST, truncated seconds, and returned the floored whole minutes since shift_start only when the delay is strictly more than 10 minutes.

2.compute_overtime():-
   # What is wrong:-
   the original code does not inforce the required 30 minute minimum  rule 
   the shift end calculation can be wrong if the supplied attendance date is not the date on which the shift started.
   # how i notice it :-
   For a shift ending at 18:00, test punch-outs at 18:29 and 18:30. Both results should follow the explicit overtime threshold.
   For a 22:00–06:00 shift, supply the shift-start date and punch out at 06:30 the next day.
   
   # How i fix it :-
    Calculate the correct shift-end datetime, including the following day for overnight shifts, and return overtime only when at least 30 complete minutes have elapsed.

    Treat date_str as the shift-start date and add one day to the shift-end date for an overnight shift.

3.compute_work_hours() :-
  # What is wrong:-
    The original code used Python round() on a float, which is not half-up rounding, and it did not truncate seconds first.
  # How I noticed it :-
    8 hours 7 minutes 30 seconds must round half-up to 8.13.
  # How I fixed it :-
    Truncated both instants to whole seconds and used Decimal with ROUND_HALF_UP to 2 decimals.
# Additional function Added :- 
    The API send datetime in format of Epoch millisecond and store in BSON format in PyMongo so that these two dunxtion is created to convert it and solve this issue.

    1.epoch_millis_to_datetime():- 
    converts Epoch milliseconds into a timezone-aware UTC datetime, ready to be stored by PyMongo as a BSON Date.



    2.datetime_to_epoch_millis():-
     converts a timezone-aware datetime back into an integer representing Epoch milliseconds.
    

## Endpoints :-

Models :=
1. There is a missing field in Employee In model which is created_at
2. there is a limit is given in a Epoch Millisecond so in Punch_In model the limit is not given.


1. @app.post("/employees", status_code=201)

In this date is directly insert at created_at field without converting epoch to BSON format 
Fix: skip = (page - 1) * page_size, 
 total = count_documents(q),
  validate page and page_size (max 100), sort by emp_code.

2. @app.get("/employees")

skip is not calculated properly 
pagination validation 
incorrect total count : This counts every employee, even when the request filters by department.

3. @app.post("/attendance/punch-in", status_code=201)

provide only shift start to compute_late_minutes
does not convert epoch to a a timezone aware datetime 

4. @app.get("/attendance")
Pagination parameters are not validated.
Calculates total incorrectly for efficient pagination.
Sorting is done in Python after retrieving all records.
Loads every attendance record into Python.

