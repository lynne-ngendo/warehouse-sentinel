# Table contracts

Each contract is a property the warehouse must hold. The check layer turns each
one into SQL and evaluates it deterministically. A violation is a fact, not a
judgment, which is why the model is never asked whether something is broken.

## fct_visit

1. **Unique grain.** `COUNT(*) = COUNT(DISTINCT visit_id)`.
   Violated by `duplication`.
2. **Freshness.** `MAX(visit_date)` is within 1 day of the current date.
3. **Source liveness.** Every `source_system` seen in the trailing 60 days has
   at least one row in the last 3 days.
   Violated by `stall`.
4. **Unit coverage.** Every `unit_id` that reported in the 56 day baseline
   window also reports in the trailing 14 day window. The baseline runs 56 days back,
   which has to start well before any gap for the check to see the unit at all.
   Violated by `coverage_gap`.
5. **Referential integrity.** Every `unit_id`, `worker_id`, and `household_id`
   resolves to its dimension.

## fct_household

6. **Tombstone consistency.** `deleted_at IS NOT NULL` implies
   `is_active = FALSE`.
   Violated by `soft_delete`.
7. **Unique grain.** `COUNT(*) = COUNT(DISTINCT household_id)`.

## Why contracts 3 and 4 matter most

Both fail while every top-line number stays normal. The `stall` costs 2 percent
of total volume and `coverage_gap` costs none at all, because the remaining
units absorb the missing work. A threshold alert on daily row count sees
nothing in either case. That is the gap the agent exists to close.
