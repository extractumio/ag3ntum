"""
Ag3ntum system tools package.

Contains system-level tools that are always available to the agent,
regardless of permission settings. All file tools use Ag3ntumPathValidator
for security validation.

Security Architecture:
- Ag3ntumBash: Uses bwrap sandbox for subprocess isolation
- Ag3ntumRead/Write/Edit/etc: Use Ag3ntumPathValidator for path validation
- Ag3ntumWebFetch: Uses domain blocklist/allowlist for network security
"""
from .ag3ntum_bash import (
    AG3NTUM_BASH_TOOL,
    create_ag3ntum_bash_mcp_server,
    is_ag3ntum_bash_tool,
)
from .ag3ntum_read import (
    create_read_tool,
    create_ag3ntum_read_mcp_server,
)
from .ag3ntum_write import (
    create_write_tool,
    create_ag3ntum_write_mcp_server,
)
from .ag3ntum_edit import (
    create_edit_tool,
    create_ag3ntum_edit_mcp_server,
)
from .ag3ntum_multiedit import (
    create_multiedit_tool,
    create_ag3ntum_multiedit_mcp_server,
)
from .ag3ntum_glob import (
    create_glob_tool,
    create_ag3ntum_glob_mcp_server,
)
from .ag3ntum_grep import (
    create_grep_tool,
    create_ag3ntum_grep_mcp_server,
)
from .ag3ntum_ls import (
    create_ls_tool,
    create_ag3ntum_ls_mcp_server,
)
from .ag3ntum_webfetch import (
    create_webfetch_tool,
    create_ag3ntum_webfetch_mcp_server,
)
from .ag3ntum_ask import (
    create_ask_user_question_tool,
    create_ag3ntum_ask_mcp_server,
    AG3NTUM_ASK_TOOL,
    get_pending_question,
    submit_answer,
)
from .ag3ntum_file_tools import (
    create_ag3ntum_tools_mcp_server,
)

__all__ = [
    # Bash tool (with bwrap sandbox)
    "AG3NTUM_BASH_TOOL",
    "create_ag3ntum_bash_mcp_server",
    "is_ag3ntum_bash_tool",
    # Unified tools server (includes Bash + all file tools)
    "create_ag3ntum_tools_mcp_server",
    # Individual tool creation functions (for advanced use cases)
    "create_read_tool",
    "create_write_tool",
    "create_edit_tool",
    "create_multiedit_tool",
    "create_glob_tool",
    "create_grep_tool",
    "create_ls_tool",
    "create_webfetch_tool",
    # Individual MCP server creation (for separate servers if needed)
    "create_ag3ntum_read_mcp_server",
    "create_ag3ntum_write_mcp_server",
    "create_ag3ntum_edit_mcp_server",
    "create_ag3ntum_multiedit_mcp_server",
    "create_ag3ntum_glob_mcp_server",
    "create_ag3ntum_grep_mcp_server",
    "create_ag3ntum_ls_mcp_server",
    "create_ag3ntum_webfetch_mcp_server",
    # AskUserQuestion tool
    "create_ask_user_question_tool",
    "create_ag3ntum_ask_mcp_server",
    "AG3NTUM_ASK_TOOL",
    "get_pending_question",
    "submit_answer",
]
