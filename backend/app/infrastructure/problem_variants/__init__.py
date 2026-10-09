"""Problem-level variant sessions (issue #685).

Session persistence and in-process execution for creating a variant from an
existing problem. The session's ``variation`` subtree mirrors the ingestion
item's ``item.variation`` shape exactly, so the lifecycle semantics,
serialization and admission gates of the batch pipeline are reused unchanged;
the atomic predicates are mirrored (not parameterized) so hot ingest code
stays untouched.
"""
