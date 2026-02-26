class AgentCoreError(Exception):
    pass


class PlanValidationError(AgentCoreError):
    pass


class ReplanLimitReachedError(AgentCoreError):
    pass


class ContractViolationError(AgentCoreError):
    pass


class MemoryLockError(AgentCoreError):
    pass


class StorageSchemaError(AgentCoreError):
    pass
