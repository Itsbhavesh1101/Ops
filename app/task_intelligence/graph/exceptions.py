"""Domain exceptions for task dependency graph analysis."""


class GraphValidationError(Exception):
    """Base exception for dependency graph validation errors."""


class UnknownTaskReferenceError(GraphValidationError):
    """Raised when a dependency references a task not defined in the graph."""

    def __init__(self, task_id: str, role: str):
        super().__init__(f"Dependency references unknown task '{task_id}' as {role}.")
        self.task_id = task_id
        self.role = role


class SelfDependencyError(GraphValidationError):
    """Raised when a task depends on itself."""

    def __init__(self, task_id: str):
        super().__init__(f"Self-dependency rejected: task '{task_id}' cannot depend on itself.")
        self.task_id = task_id


class DuplicateDependencyError(GraphValidationError):
    """Raised when a dependency edge is defined multiple times."""

    def __init__(self, predecessor_id: str, successor_id: str):
        super().__init__(
            f"Duplicate dependency rejected: '{predecessor_id}' -> '{successor_id}' already exists."
        )
        self.predecessor_id = predecessor_id
        self.successor_id = successor_id


class GraphCycleError(GraphValidationError):
    """Raised when one or more cycles are detected in the dependency graph."""

    def __init__(self, cycle_path: list[str]):
        path_str = " -> ".join(cycle_path)
        super().__init__(f"Dependency graph contains a cycle: {path_str}")
        self.cycle_path = cycle_path
