"""
Agent exceptions.

Custom exception classes for Ag3ntum.
"""


class AgentError(Exception):
    """Base exception for agent errors."""
    pass


class SessionIncompleteError(AgentError):
    """Session did not complete properly."""
    pass


class MaxTurnsExceededError(AgentError):
    """Exceeded the maximum number of turns."""
    pass


class ServerError(AgentError):
    """API/server error occurred."""
    pass


class TaskError(AgentError):
    """Error reading or processing task."""
    pass


class PermissionError(AgentError):
    """Permission-related error."""
    pass


class SessionError(AgentError):
    """Session management error."""
    pass


class SkillError(AgentError):
    """Error related to skill loading or execution."""
    pass

