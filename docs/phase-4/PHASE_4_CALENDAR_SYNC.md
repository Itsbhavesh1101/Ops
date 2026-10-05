# Phase 4: Calendar Boundary Adapter & Secondary Mapping

## 1. Architectural Separation & Secondary Mapping
Calendar synchronization operates as a secondary, integration boundary. Internal scheduling domain entities (`schedules`, `schedule_assignments`) are never coupled to external calendar IDs. Instead, a dedicated mapping table `calendar_links` persists the external relationship:
- `id`: UUID primary key
- `tenant_id`: Mandatory tenant isolation
- `schedule_id`: Foreign key to `schedules.id`
- `assignment_id`: Identifier of the internal activity/assignment
- `provider`: Provider identifier (e.g., `GOOGLE`, `OUTLOOK`, `ICAL`)
- `external_calendar_id`: External calendar identifier
- `external_event_id`: External event identifier
- `sync_status`: Enum (`SYNCED`, `PENDING`, `FAILED`)
- `sync_token` / `etag`: Optimistic concurrency tokens
- `last_synced_at`: Timestamp of last successful sync
- `error_message`: Error diagnostics on sync failure

## 2. RFC 5545 iCalendar Engine
The canonical serializer and parser (`app/integrations/calendar/rfc5545.py`) adheres strictly to RFC 5545:
- Zero third-party library dependencies (pure Python standard library).
- Emits UTC formatted timestamps (`YYYYMMDDTHHMMSSZ`).
- Includes `UID`, `DTSTAMP`, `DTSTART`, `DTEND`, `SUMMARY`, `DESCRIPTION`, `LOCATION`, and `STATUS`.
- Escapes special characters (commas, semicolons, backslashes, newlines) and folds lines > 75 octets.
- Full round-trip parsing and validation verified by unit tests.

## 3. Truthful Degradation & Outage Handling
External calendar providers may experience network partitions, 5xx outages, or rate limiting. The system enforces strict failure honesty:
- **No False Successes**: If the external adapter raises an error or fails to respond, `calendar_links.sync_status` is updated to `FAILED` with the exact error details.
- **Schedule Persistence Isolated**: External calendar failures never cause a rollback of the primary PostgreSQL transaction publishing the schedule.
- **Reconciliation**: `CalendarReconciliationService` performs background verification of published assignments against `calendar_links`, detecting drift and re-enqueuing failed sync operations.
