COORDINATOR_INSTRUCTION = (
    "ADK scaffold coordinator. Delegate planning/execution via sub-agents and "
    "emit concise response summaries."
)

PLANNER_INSTRUCTION = (
    "Use MCP discovery tools to identify and load relevant skills, then "
    "produce concise planning guidance. Never spawn subagents."
)

EXECUTOR_INSTRUCTION = (
    "Use only allowed MCP skills for this step and return concise execution output. "
    "Execute exactly one step and never spawn subagents."
)

PLANNER_SCAFFOLD_PREFIX = "planner_scaffold: analyzed request"
EXECUTOR_SCAFFOLD_PREFIX = "executor_scaffold: prepared step output for"
