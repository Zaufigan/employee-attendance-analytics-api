# REVIEW.md

| # | Where (function / line) | What is wrong                                                                           | How you'd notice it (test, input, or symptom)                    | How you fixed it                                                                                                 |
| - | ----------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1 | `create_employee`       | Check-then-insert can cause a race condition during concurrent employee creation.       | Send concurrent requests using the same `emp_code`.              | Removed the separate existence check and handled `DuplicateKeyError` using the unique index, returning HTTP 409. |
| 2 | `list_employees`        | Pagination parameters were not validated, and results did not have a stable sort order. | Try `page=0`, an invalid `page_size`, or request multiple pages. | Added `Query` validation and sorted results by `emp_code`.
## Things Reviewed but Not Considered Defects

* A unique index on `emp_code` prevents duplicate employee records.
* A compound unique index on `emp_code` and `date` prevents duplicate daily attendance records.
* Missing attendance records on non-working days are not necessarily defects; analytics should follow the API contract.
* A punch-out request should update an existing open attendance record rather than create a second attendance record.
