# Release note 2026.09.1

Owner: Platform Operations
Rollback: redeploy the previous tag; the queue drains before the worker stops.

The worker now refuses a job whose manifest digest does not match the one it
was scheduled with.
