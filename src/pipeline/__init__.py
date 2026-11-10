"""The offline pipeline that produces this project's own TBox/ABox Turtle
files (see ingest/ and enrich/) -- crawl, then transform+link. Not part of
the live retrieval system: nothing under domain/, infrastructure/, or
application/ imports from here, and nothing here imports from them
either (verified; the only thing shared is config.py's values).
Run once (or whenever the source data changes), not on every request."""
