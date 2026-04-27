"""Per-endpoint tests have been migrated to:

  - tests/test_client_contract.py    (parametrized HTTP contract)
  - tests/test_client_transport.py   (transport-layer error handling)
  - tests/test_client_envelope_contract.py (v0.1 envelope unwrapping)

This stub exists only to prevent stale test-discovery cache. Delete in a
follow-up commit once CI has run cleanly with the new layout.
"""
