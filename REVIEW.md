# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.
# REVIEW.md

| - | ----------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1 | `create_employee`       | Check-then-insert can cause a race condition when concurrent requests create the same employee. | Send concurrent requests with the same `emp_code`.              | Removed the separate existence check and handled `DuplicateKeyError` using the unique index, returning HTTP 409. |
| 2 | `list_employees`        | Pagination parameters may not be validated.                                                     | Try `page=0` or a negative `page_size`.                         | Validate pagination parameters and return HTTP 422 for invalid values.                                           |
| 3 | `list_attendance`       | Date filters may accept invalid dates or an incorrect date range.                               | Submit an invalid date or a start date later than the end date. | Validate date formats and ensure the start date is not after the end date.                                       |
| 4 | `compute_overtime`      | Overtime calculation may not handle overnight shifts correctly.                                 | Test attendance for a shift that crosses midnight.              | Calculate duration using complete timestamps and the defined shift rules.                                        |
| 5 | `regularize_attendance` | Concurrent corrections may overwrite newer changes.                                             | Submit simultaneous corrections for the same attendance record. | Use conditional updates or concurrency control to prevent stale updates.                                         |

## Things Reviewed but Not Considered Defects

* Unique indexes on employee codes and employee-date attendance records help prevent duplicate records.
* Conditional updates during punch-out help prevent duplicate punch-outs.
* Missing attendance on non-working days may be valid, provided analytics follow the API contract.
