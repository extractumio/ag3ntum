"""
Task management for Ag3ntum.

Handles reading tasks from files and CLI, and managing task state.
"""
import logging
from pathlib import Path
from typing import Optional

from .exceptions import TaskError

logger = logging.getLogger(__name__)


class TaskManager:
    """
    Manages task loading and processing.

    Tasks can be loaded from:
    - A custom file path via --task-file
    - Directly from a string via --task
    """

    def __init__(self, working_dir: Optional[Path] = None) -> None:
        """
        Initialize the task manager.

        Args:
            working_dir: Working directory for task files.
        """
        self._working_dir = working_dir or Path.cwd()

    def load_from_file(self, file_path: str) -> str:
        """
        Load task from a file.

        Args:
            file_path: Path to task file (absolute or relative to working dir).

        Returns:
            The task content as a string.

        Raises:
            TaskError: If the file cannot be read.
        """
        task_path = Path(file_path)
        if not task_path.is_absolute():
            task_path = self._working_dir / task_path

        if not task_path.exists():
            raise TaskError(f"Task file not found: {task_path}")

        try:
            content = task_path.read_text().strip()
            if not content:
                raise TaskError(f"Task file is empty: {task_path}")

            logger.info(f"Loaded task from {task_path}")
            return content
        except IOError as e:
            raise TaskError(f"Failed to read task file {task_path}: {e}") from e

    def load_from_string(self, task: str) -> str:
        """
        Load task from a string.

        Args:
            task: The task content as a string.

        Returns:
            The task content (validated).

        Raises:
            TaskError: If the task is empty.
        """
        content = task.strip()
        if not content:
            raise TaskError("Task cannot be empty")

        return content

    def load(
        self,
        task: Optional[str] = None,
        file_path: Optional[str] = None
    ) -> str:
        """
        Load task from string or file.

        Priority:
        1. If task string is provided, use it
        2. If file_path is provided, load from that file
        3. Otherwise, look for input/task.md in working directory

        Args:
            task: Task content as a string.
            file_path: Path to task file.

        Returns:
            The task content.

        Raises:
            TaskError: If no task can be loaded.
        """
        if task:
            return self.load_from_string(task)

        # Use provided file_path or default to input/task.md
        default_path = "input/task.md"
        return self.load_from_file(file_path or default_path)


def load_task(
    task: Optional[str] = None,
    file_path: Optional[str] = None,
    working_dir: Optional[Path] = None
) -> str:
    """
    Convenience function to load a task.

    Args:
        task: Task content as a string.
        file_path: Path to task file.
        working_dir: Working directory for task files.

    Returns:
        The task content.
    """
    manager = TaskManager(working_dir)
    return manager.load(task, file_path)
