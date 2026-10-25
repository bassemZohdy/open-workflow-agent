"""Release qualification gate for executable background jobs.

This is intentionally internal and has no deployment configuration binding.
The finite Windows/Linux embedded-SQLite profile passed all six source-matched
workflows at d72781f. Deployment opt-in and platform/request admission remain
mandatory. This does not qualify PostgreSQL/external supervisors or JavaScript
background execution.
"""

BACKGROUND_EXECUTION_PROFILE_QUALIFIED = True
