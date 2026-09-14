# Release note 2026.09.2

Owner: Platform Operations

The worker now refuses a job whose manifest digest does not match the one it
was scheduled with. (No rollback line: the manifest requires one, so this file
is flagged by the deterministic check.)
