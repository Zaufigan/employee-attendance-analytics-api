# DECISIONS.md  (about 150-200 words in total - plain language, your own word)
## 1. Indexes

I created the following indexes:

* **Unique index on `employees.emp_code`:** Prevents duplicate employee codes and supports employee lookups by employee code.
* **Compound unique index on `attendance_logs.emp_code` and `attendance_logs.date`:** Ensures that an employee has at most one attendance record per date and supports queries using these fields.

I considered creating a separate index on `attendance_logs.date`, but did not add it because the current compound index already supports queries that filter by `emp_code` and `date`. A separate date index could be useful for date-only queries across all employees, depending on query volume.

## 2. Punch-in Race

If two identical punch-in requests arrive simultaneously, both may initially check for an existing attendance record and find none. Both then attempt to insert a record.

The unique compound index on `emp_code` and `date` allows only one insert to succeed. The other insert raises `DuplicateKeyError`, which the API catches and returns as HTTP 409 Conflict.

This database-level constraint prevents duplicate attendance records even when requests arrive concurrently.

## 3. Ties

The leaderboard sorts employees by late minutes and applies the configured limit. If two employees are tied at the cutoff, the result depends on the sort fields and tie-breaking rules implemented in the query.

A deterministic tie-breaker, such as `emp_code`, can make results consistent. If all employees tied at the cutoff must be included, the implementation must explicitly support that behaviour rather than applying a strict limit.

## 4. Headcount

The department summary should start from the employees collection and then associate attendance data with each employee. This ensures that employees with zero attendance logs are still included in department headcount.

If the aggregation starts from attendance logs instead, employees without logs will be excluded. Starting from employees avoids this problem.

## 5. One Change for 100x the Data

I would optimize analytics queries to perform more aggregation inside MongoDB instead of loading large datasets into Python memory. I would also review query patterns, add indexes where justified by query plans, and use pagination or bounded date ranges to control the amount of data processed per request.
