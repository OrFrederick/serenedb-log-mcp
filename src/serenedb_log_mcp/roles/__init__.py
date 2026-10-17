"""The Role layer (T7): answer frames, the Manager's not-measured list, the Role shaping and the briefs.

The Roles differ in what the server returns, not only in tone (#4): `shape_for_role` is the Role
hook that `toolkit` applies to every Evidence result.
"""

from serenedb_log_mcp.roles.frames import (
    NEXT_STEP_KIND,
    NOT_MEASURED,
    SECTIONS,
    answer_frame,
    not_measured,
)
from serenedb_log_mcp.roles.shaping import (
    RAW_LINE_TOOLS,
    TEAM_LEAD_SAMPLE_ROWS,
    is_raw_line,
    shape_for_role,
)

__all__ = [
    "NEXT_STEP_KIND",
    "NOT_MEASURED",
    "RAW_LINE_TOOLS",
    "SECTIONS",
    "TEAM_LEAD_SAMPLE_ROWS",
    "answer_frame",
    "is_raw_line",
    "not_measured",
    "shape_for_role",
]
