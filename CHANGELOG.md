## v0.4.0 (2026-06-20)

### Feat

- **cli**: add backup command to export database and receipt images (#17)
- **web**: add authentication and role-based access control (#14)
- **ingest**: import receipts from a watched folder (#13)
- **web**: add Apple shortcut for receipt ingestion (#12)
- **export**: add CSV and JSON data export to web and CLI (#11)
- normalize unit prices for honest store comparison (#10)
- **cost**: track and display LLM parsing cost per receipt (#9)
- **receipts**: reparse a receipt from its stored image (#8)
- **web**: refine dashboard, admin, upload, and chart UI (#7)
- **products**: merge singular and plural product variants on ingest (#6)

### Fix

- **web**: hide admin controls that would remove the last admin

### Refactor

- **cli**: reduce CLI to the serve command for web-only use (#16)
- improve module organization and naming (#15)

## v0.3.0 (2026-06-17)

### Feat

- **docker**: apply container timezone from TZ env var (#4)

### Fix

- **deps**: declare pyyaml as a runtime dependency (#5)

## v0.2.0 (2026-06-17)

### Feat

- **llm**: make the LLM provider configurable (#3)
- **docker**: add container image and compose for self-hosting
- **admin**: add duplicate product and store merging
- **categories**: add managed taxonomy with LLM reclassification
- **web**: add HTMX interface with dashboard, charts, and uploads
- **analytics**: add spend and price-history queries with a CLI
- add receipt ingestion backend with data model and worker queue
