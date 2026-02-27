from typing import Any


class AgentCoreError(Exception):
    pass


class PlanValidationError(AgentCoreError):
    def __init__(self, message: str, failure_response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.failure_response = failure_response


class ReplanLimitReachedError(AgentCoreError):
    def __init__(self, message: str, failure_response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.failure_response = failure_response


class ContractViolationError(AgentCoreError):
    pass


class MemoryLockError(AgentCoreError):
    pass


class StorageSchemaError(AgentCoreError):
    pass
