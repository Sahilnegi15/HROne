# DECISIONS.md  (about 150-200 words in total - plain language, your own words)
 
1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
   On `employees` I created a unique `emp_code` index, so duplicate creates fail in the database and list sorting is fast. I also created `(department, joined_on)` for headcount. On `attendance_logs` I created a unique `(emp_code, date)` index for one record per day, plus monthly lookups and PATCH. I also created `(date, emp_code)` for the list sort and date-range scans, and `(emp_code, punch_in desc)` for punch-out. I rejected a plain single-field `date` index, because the compound indexes already cover it.
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
   Both requests may pass the existence check and try to insert. The unique `(emp_code, date)` index lets the database accept only one. The other insert raises `DuplicateKeyError`, which I catch and return as 409. Exactly one request gets 201.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
   I use standard competition ranking, so tied employees share a rank and the next rank is skipped (1, 2, 2, 4). The limit applies to the rank, not the row count. Everyone whose rank is within the limit is returned, so the list can be longer than `limit`. Rows are ordered by late minutes descending, then `emp_code`.
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
   The aggregation starts from `employees`, filtered by `joined_on <= month end`, and then does a `$lookup` into the logs. Employees with no logs stay in the pipeline with an empty log list, so they are still counted.
5. **One thing you would change** if this had to serve 100x the data.
   I would store pre-computed daily summaries per department and update them on punch and correction, so analytics stop scanning raw logs. We can also fix the pagination issue: `skip` takes too much time and gets slow for 100,000 entries.
 