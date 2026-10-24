### `docs/isolation.md`
This fulfills Milestone 2's laboratory exercise documenting PostgreSQL's concurrency behaviors across two side-by-side terminal sessions.

```markdown
# Transaction Isolation Level Experiments

### Case 1: Read Committed (Postgres Default)
- **Session A:**
  ```sql
  BEGIN TRANSACTION ISOLATION LEVEL READ COMMITTED;
  SELECT balance FROM accounts WHERE id = 1; -- Returns 100000