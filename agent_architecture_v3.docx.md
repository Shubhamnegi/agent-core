**Agentic Service**

Architecture Blueprint

*v3.0  ·  Manager–Planner–Executor model  ·  OpenSearch-unified  ·  Storage & Chat decoupled*

| Attribute | Value |
| :---- | :---- |
| Version | 3.0 — Full rework with four-role model |
| Storage | OpenSearch only (vector \+ document) |
| Skill Access | cloud\_skill\_service env var — auto-scoped, no explicit gating in agent code |
| Roles | Manager · Planner (SubAgent-A) · Executor (SubAgent-B×n) · Infrastructure |
| Plan | First-class persisted object with step-level lifecycle |
| Replanning | Up to 3 attempts with surgical revision — completed steps preserved |
| Large Data | Content-length gate → temp file → line sampling → Python synthesis |
| Memory | Task-scoped namespaced keys · optimistic locking on write |
| Monitoring | Four-layer observability — trace · subagent events · skill audit · plan health |
| Decoupled | Agentic Service · Storage Adapter · Chat API — three hard boundaries |

# **1\. Four-Role Model**

The entire system is built around one core analogy: the Orchestrator is the manager, SubAgents are employees. Managers delegate and synthesise. Employees execute and report back. Employees never hire other employees — if a task is bigger than one step, they surface it and let the manager decide.

| MANAGER | Orchestrator (AgentCore) | Receives user task. Drives plan execution. Handles replanning. Synthesises final response. Never touches a skill directly. |
| :---: | :---- | :---- |

| PLANNER | SubAgent-A | Discovers skills via MCP. Loads manifests. Reranks candidates. Produces a progressive TODO plan with hard return\_specs. Verifies plan against skill schemas before returning. |
| :---: | :---- | :---- |

| EXECUTOR | SubAgent-B (n instances) | Executes one plan step. Calls skills via MCP. Handles large responses internally. Validates own output against return\_spec before writing to memory. |
| :---: | :---- | :---- |

| INFRASTRUCTURE | Infra Tool Suite | write\_memory · read\_memory · write\_temp · read\_lines · exec\_python. Always available to all subagents. Not skill-gated. Enforces namespacing, locking, and contract validation. |
| :---: | :---- | :---- |

| HARD RULE | SubAgents cannot spawn SubAgents. If a SubAgent-B determines the task exceeds a single step, it returns a structured { status: insufficient } signal. The Orchestrator decides what to do next. Authority boundary is absolute. |
| :---: | :---- |

| HARD RULE | Skill access is scoped automatically by cloud\_skill\_service env var. No agent code knows about access control — it simply calls the Skill Service and receives only what it is permitted to see. |
| :---: | :---- |

# **2\. End-to-End Execution Flow**

The full lifecycle of a user task through the four-role model:

## **2.1 Orchestrator receives task**

* Chat API calls POST /agent/run with { tenant\_id, user\_id, session\_id, message }

* Orchestrator loads soul (persona/system prompt) from SoulStore

* Orchestrator creates a Plan object in agent\_plans index — status: pending

* Orchestrator spawns SubAgent-A with the task description

## **2.2 SubAgent-A — Skill Scouting and Planning**

* Calls find\_relevant\_skills(task\_description) via MCP — receives candidate list scoped by cloud\_skill\_service

* Calls load\_skill(skill\_name) for each promising candidate — may load 2–6 skills

* Reads full manifests — understands input/output schemas, capabilities, limitations

* Reranks — eliminates noise, identifies exact skills needed for the task

* Runs verification pass: checks that each planned step's return\_spec is satisfiable by the skill's declared output schema

* Returns a progressive TODO plan to Orchestrator:

{  
  "plan\_id": "plan\_abc",  
  "steps": \[  
    {  
      "step": 1,  
      "task": "List all active outlets",  
      "skills": \["skill\_outlet\_list"\],  
      "return\_spec": {  
        "shape": { "outlet\_ids": "array\<string\>" },  
        "reason": "step 2 needs only ids for analytics"  
      }  
    },  
    {  
      "step": 2,  
      "task": "Run engagement analytics on outlet\_ids from step 1",  
      "skills": \["skill\_analytics"\],  
      "input\_from\_step": 1,  
      "return\_spec": {  
        "shape": { "results": "array\<{outlet\_id, score, rank}\>" },  
        "reason": "orchestrator needs ranked list for final response"  
      }  
    }  
  \],  
  "max\_steps": 10,  
  "replan\_count": 0  
}

| NOTE | Plan steps are bounded at 10 maximum. SubAgent-A must produce a plan within this limit. If the task cannot be broken into ≤10 steps, SubAgent-A returns an infeasibility signal and Orchestrator returns a structured failure to Chat API. |
| :---: | :---- |

## **2.3 Orchestrator — Plan execution loop**

* Persists the plan to agent\_plans index — status: executing

* Reads Step 1: spawns SubAgent-B1 with task \+ allowed\_skills: \[skill\_outlet\_list\] \+ return\_spec

* Waits for SubAgent-B1 completion signal via message bus

* Validates SubAgent-B1 output against return\_spec (via write\_memory contract enforcement)

* Reads Step 2: spawns SubAgent-B2 with task \+ input\_key from Step 1 memory \+ allowed\_skills: \[skill\_analytics\]

* Continues until all steps complete or a failure triggers replanning

* On all steps complete: synthesises final response from memory outputs, returns to Chat API

## **2.4 SubAgent-B — Execution with large data handling**

* Receives task \+ allowed\_skills\[\] \+ return\_spec from Orchestrator

* Calls skill(s) via MCP using allowed skills only

* On MCP response — content-length gate:

  * If response \< threshold (e.g. 50KB): push directly to context

  * If response ≥ threshold:

    * write\_temp(response) → file\_id

    * read\_lines(file\_id, 0, 20\) → sample

    * LLM infers data model from sample

    * LLM writes Python extraction script targeting only fields in return\_spec

    * exec\_python(script, file\_id) → extracted result

    * Use extracted result as working data

* Constructs output matching return\_spec shape exactly

* Self-validates output against return\_spec before calling write\_memory

* write\_memory(key, output, return\_spec) — infra tool enforces schema, namespaces key, acquires lock

* Emits subagent.finish event to message bus

## **2.5 SubAgent-B — Insufficient signal**

If SubAgent-B determines the task cannot be completed in a single execution:

{  
  "status": "insufficient",  
  "completed": null,  
  "reason": "outlet data requires 3 transformation passes, single step cannot handle",  
  "suggestion": "split into: normalize → filter → aggregate"  
}

Orchestrator receives this, increments replan\_count, and triggers SubAgent-A replanning.

# **3\. Replanning**

Replanning is surgical — it revises only the remaining steps, never redoes completed steps. Maximum 3 replan attempts per session before structured failure.

## **3.1 Replan triggers**

* SubAgent-B returns status: insufficient

* SubAgent-B returns status: failed (skill error, schema mismatch, timeout)

* write\_memory contract validation fails (output does not match return\_spec)

## **3.2 Replan flow**

replan\_count \< 3:  
  → spawn SubAgent-A with:  
      original\_task,  
      completed\_steps: \[ step1\_result, step2\_result, ... \],  
      failed\_step: { step\_n, failure\_reason, suggestion },  
      remaining\_steps: \[ step\_n, step\_n+1, ... \]  
  → SubAgent-A returns revised plan (remaining steps only)  
  → Orchestrator merges: completed\_steps \+ revised\_remaining  
  → execution continues from next step

replan\_count \== 3:  
  → Orchestrator returns structured failure to Chat API:  
    {  
      "status": "failed",  
      "reason": "max replan attempts reached",  
      "completed\_steps": \[...\],  
      "last\_failure": { step, reason }  
    }

| KEY DESIGN | SubAgent-A during replanning receives what was already completed so it does not re-plan those steps. It only produces revised steps for the remaining work. Completed memory keys remain valid and available to the revised plan. |
| :---: | :---- |

## **3.3 Plan lifecycle states**

| State | Meaning |
| :---- | :---- |
| pending | Plan object created, SubAgent-A spawned |
| planning | SubAgent-A running |
| executing | Orchestrator iterating steps, SubAgent-Bs running |
| replanning | Replan triggered, SubAgent-A re-spawned with context |
| complete | All steps done, final response synthesised |
| failed | 3 replan attempts exhausted or infeasibility signal received |

# **4\. Memory Design**

## **4.1 Memory key namespacing**

All memory keys are constructed automatically by the write\_memory infra tool. SubAgents pass only a short label. The tool builds the full namespaced key:

// SubAgent calls:  
write\_memory("outlet\_ids", data, return\_spec)

// Infra tool constructs full key:  
"{tenant\_id}:{session\_id}:{task\_id}:{subagent\_defined\_key}"  
// e.g. "brand\_123:sess\_456:task\_789:outlet\_ids"

// Orchestrator reads using resolved key from plan:  
read\_memory("brand\_123:sess\_456:task\_789:outlet\_ids")

## **4.2 Optimistic locking**

* write\_memory acquires a lock on the full namespaced key for the duration of write \+ orchestrator read cycle

* If a concurrent write arrives for the same key from a different task\_id, it waits (max 5s) then fails with MemoryLockError

* Lock is released after orchestrator confirms it has read the value for that plan step

* Within a single session, concurrent plan steps are rare — locking cost is negligible

* For future parallel step execution: locks prevent race conditions without requiring distributed transactions

## **4.3 Contract enforcement at write\_memory**

write\_memory is the single choke point for output validation. It enforces the hard contract:

write\_memory(key, data, return\_spec):  
  1\. Validate data shape against return\_spec.shape (JSON Schema check)  
  2\. If validation fails:  
       → do NOT write  
       → return { status: contract\_violation, expected: return\_spec.shape, actual: data\_shape }  
       → SubAgent-B surfaces this as a failure signal  
       → Orchestrator treats as step failure, triggers replan  
  3\. If validation passes:  
       → acquire lock  
       → write to agent\_memory with namespace key  
       → emit memory.upsert event  
       → release lock after orchestrator read confirmation

## **4.4 Memory index (agent\_memory) — updated schema**

PUT /agent\_memory  
{  
  "settings": { "index": { "knn": true } },  
  "mappings": {  
    "dynamic": "strict",  
    "properties": {  
      "id":           { "type": "keyword" },  
      "tenant\_id":    { "type": "keyword" },  
      "session\_id":   { "type": "keyword" },  
      "task\_id":      { "type": "keyword" },  
      "user\_id":      { "type": "keyword" },  
      "scope":        { "type": "keyword" },  
      "key":          { "type": "keyword" },  
      "text":         { "type": "text" },  
      "embedding":    { "type": "knn\_vector", "dimension": 1536,  
                        "method": { "name": "hnsw", "engine": "nmslib" } },  
      "source":       { "type": "keyword" },  
      "step\_index":   { "type": "integer" },  
      "plan\_id":      { "type": "keyword" },  
      "locked\_by":    { "type": "keyword" },  
      "lock\_expires": { "type": "date" },  
      "created\_at":   { "type": "date" },  
      "ttl\_at":       { "type": "date" }  
    }  
  }  
}

# **5\. Plan as a First-Class Object**

The plan is persisted to OpenSearch from the moment SubAgent-A returns it. Every state transition, step outcome, and replan revision is recorded. This enables auditability, resumability, and debugging.

## **5.1 agent\_plans index schema**

PUT /agent\_plans  
{  
  "mappings": {  
    "dynamic": "strict",  
    "properties": {  
      "plan\_id":        { "type": "keyword" },  
      "session\_id":     { "type": "keyword" },  
      "tenant\_id":      { "type": "keyword" },  
      "user\_id":        { "type": "keyword" },  
      "status":         { "type": "keyword" },  
      "replan\_count":   { "type": "integer" },  
      "created\_at":     { "type": "date" },  
      "completed\_at":   { "type": "date" },  
      "steps": {  
        "type": "nested",  
        "properties": {  
          "step\_index":      { "type": "integer" },  
          "task":            { "type": "text" },  
          "skills":          { "type": "keyword" },  
          "return\_spec":     { "type": "object", "enabled": false },  
          "input\_from\_step": { "type": "integer" },  
          "status":          { "type": "keyword" },  
          "task\_id":         { "type": "keyword" },  
          "memory\_key":      { "type": "keyword" },  
          "validated":       { "type": "boolean" },  
          "failure\_reason":  { "type": "text" },  
          "started\_at":      { "type": "date" },  
          "finished\_at":     { "type": "date" }  
        }  
      },  
      "replan\_history": {  
        "type": "nested",  
        "properties": {  
          "attempt":     { "type": "integer" },  
          "trigger":     { "type": "keyword" },  
          "failed\_step": { "type": "integer" },  
          "reason":      { "type": "text" },  
          "revised\_at":  { "type": "date" }  
        }  
      }  
    }  
  }  
}

## **5.2 What persisting the plan enables**

| Capability | Detail |
| :---- | :---- |
| Auditability | Full record of what was planned, what ran, what failed, how it was revised — per session |
| Resumability | If worker crashes mid-plan, Orchestrator can reload plan from OpenSearch and continue from last completed step |
| Debugging | Which step failed? What was the return\_spec? Did write\_memory reject the output? All visible. |
| Replan transparency | replan\_history shows every revision — trigger, failed step, attempt number |
| Analytics | Aggregate plan data shows which task types replan most, which skills cause most failures |

# **6\. Large MCP Response Handling**

MCP skill responses can be arbitrarily large. Pushing large payloads directly into LLM context bloats the subagent, degrades quality, and risks token limit errors. SubAgent-B handles this internally — Orchestrator never sees raw data.

## **6.1 Content-length gate**

MCP skill returns response  
  │  
  ├── size \< 50KB  
  │     → push directly to SubAgent-B context  
  │  
  └── size ≥ 50KB  
        → write\_temp(response)  → file\_id  
        → read\_lines(file\_id, 0, 20\)  → sample  
        → LLM reads sample, infers data model \+ structure  
        → LLM writes Python extraction script:  
              \- targets only fields declared in return\_spec  
              \- filters, maps, aggregates as needed  
        → exec\_python(script, file\_id)  → extracted\_result  
        → extracted\_result enters context (small, precise)  
        → temp file scheduled for cleanup on task completion

## **6.2 Example — 1MB menu response**

Scenario: SubAgent-B needs to extract item categories from a 1MB menu payload for downstream analytics.

\# SubAgent-B receives 1MB menu from skill\_menu\_service MCP  
file\_id \= write\_temp(menu\_response)   \# writes to isolated task temp dir

sample \= read\_lines(file\_id, 0, 20\)   \# first 20 lines  
\# LLM infers: JSON array of { item\_id, name, category, price, ... }

\# LLM writes extraction script (return\_spec needs: \[{item\_id, category}\])  
script \= '''  
import json  
with open(input\_file) as f:  
    data \= json.load(f)  
result \= \[{'item\_id': x\['item\_id'\], 'category': x\['category'\]} for x in data\]  
print(json.dumps(result))  
'''

result \= exec\_python(script, file\_id)  \# runs in sandbox, no network  
\# result: \[{item\_id, category}\] — small, matches return\_spec exactly

write\_memory('menu\_categories', result, return\_spec)

## **6.3 Infra tool: exec\_python safety**

* Runs in sandboxed subprocess — no network access, no filesystem access outside temp dir

* Max execution time: 30s (configurable per task)

* Output size limit: 500KB — if extraction script produces \> 500KB, SubAgent-B receives an oversized\_output error and must write a more selective script

* Script hash is logged to agent\_events for audit

* Temp dir is wiped on task completion — no persistence between tasks

## **6.4 Temp file lifecycle**

| Lifecycle Stage | Detail |
| :---- | :---- |
| Created | write\_temp() call within SubAgent-B execution |
| Scoped to | task\_id — isolated per subagent run |
| Cleanup trigger | subagent.finish event received by worker |
| Fallback cleanup | ILM job sweeps temp dirs older than 1 hour |
| Container isolation | If subagents run in containers, temp dir is wiped automatically on container exit |

# **7\. Infrastructure Tool Suite**

Infrastructure tools are always available to all SubAgents regardless of allowed\_skills. They are built into the subagent runtime and are not gated by cloud\_skill\_service. They handle cross-cutting concerns: memory, temp storage, and code execution.

| Tool | Signature | Behaviour |
| :---- | :---- | :---- |
| write\_memory | write\_memory(key, data, return\_spec) | Validates data against return\_spec schema. Acquires lock. Namespaces key as {tenant}:{session}:{task}:{key}. Writes to agent\_memory. Emits memory.upsert event. |
| read\_memory | read\_memory(namespaced\_key) | Reads value from agent\_memory by full namespaced key. Used by Orchestrator between steps. |
| write\_temp | write\_temp(data) → file\_id | Writes large data to task-scoped temp directory. Returns file\_id. Auto-cleaned on task completion. |
| read\_lines | read\_lines(file\_id, start, n) → lines\[\] | Reads N lines from temp file starting at offset. Used for data model inference on large payloads. |
| exec\_python | exec\_python(script, file\_id) → result | Runs Python extraction script in sandboxed subprocess against temp file. No network. 30s timeout. 500KB output limit. Logs script hash to agent\_events. |

# **8\. Skill Service Integration**

The Skill Service is consumed as an external dependency. Access scoping is handled entirely by cloud\_skill\_service — no agent code knows about access control. SubAgent-A uses it for discovery. SubAgent-B uses it for execution.

## **8.1 Access scoping — transparent by design**

\# cloud\_skill\_service env var is set at worker startup  
\# e.g. SKILL\_SERVICE\_URL=https://skills.internal/api/brand\_123

\# SubAgent-A calls:  
find\_relevant\_skills('analytics for outlet performance')  
\# → Skill Service returns only skills permitted for this brand's API key  
\# → SubAgent-A has no knowledge that filtering occurred  
\# → Internal skills for other brands are invisible

## **8.2 SubAgent-A MCP tool calls**

| MCP Tool | Purpose |
| :---- | :---- |
| find\_relevant\_skills(query) | Returns candidate skills with short descriptions. Scoped by cloud\_skill\_service automatically. |
| load\_skill(skill\_name) | Returns full skill manifest: description, input schema, output schema, execution\_mode, endpoint. SubAgent-A calls this for each candidate to build reranked plan. |

SubAgent-A calls load\_skill for multiple candidates — typically 2–6. It reads the output schemas to verify that each step's return\_spec is satisfiable. This is the verification pass before the plan is returned to Orchestrator.

## **8.3 SubAgent-B MCP tool calls**

* SubAgent-B receives allowed\_skills\[\] from Orchestrator (set from plan step definition)

* Calls skill execution via MCP using only the skills in allowed\_skills

* Skill Service enforces the allowed list — SubAgent-B cannot escalate permissions

* Every skill call is logged to agent\_events: { skill\_id, tenant\_id, user\_id, input\_size\_bytes, response\_size\_bytes, duration\_ms, ts }

# **9\. OpenSearch Index Schemas**

Five indices. All use dynamic: strict. All writes go through Storage Adapter which validates against a local JSON Schema before calling OpenSearch. A schema violation throws StorageSchemaError and is never silently dropped.

| Index | Purpose |
| :---- | :---- |
| agent\_memory | Long-term memory. KNN vector search \+ structured filters. Holds subagent outputs, summaries, cross-session facts. |
| agent\_souls | Persona and config per tenant/user. System prompts, tone, policies. |
| agent\_sessions | Session metadata and recent message window. Used by PromptBuilder for context. |
| agent\_plans | Plans as first-class objects. Step-level lifecycle, replan history, memory key references. |
| agent\_events | Append-only audit \+ event sourcing. All skill calls, memory writes, subagent lifecycle events. |

agent\_memory and agent\_plans schemas are defined in Sections 4 and 5\. The remaining three follow below.

## **9.1 — agent\_souls**

PUT /agent\_souls  
{ "mappings": { "dynamic": "strict", "properties": {  
  "id":           { "type": "keyword" },  
  "tenant\_id":    { "type": "keyword" },  
  "user\_id":      { "type": "keyword" },  
  "key":          { "type": "keyword" },  
  "persona": { "type": "object", "properties": {  
    "name":          { "type": "keyword" },  
    "system\_prompt": { "type": "text" },  
    "tone":          { "type": "keyword" },  
    "language":      { "type": "keyword" }  
  }},  
  "policies":     { "type": "object", "enabled": false },  
  "updated\_at":   { "type": "date" }  
}}}

## **9.2 — agent\_sessions**

PUT /agent\_sessions  
{ "mappings": { "dynamic": "strict", "properties": {  
  "session\_id":  { "type": "keyword" },  
  "tenant\_id":   { "type": "keyword" },  
  "user\_id":     { "type": "keyword" },  
  "state":       { "type": "keyword" },  
  "started\_at":  { "type": "date" },  
  "last\_active": { "type": "date" },  
  "messages": { "type": "nested", "properties": {  
    "msg\_id":  { "type": "keyword" },  
    "role":    { "type": "keyword" },  
    "content": { "type": "text" },  
    "ts":      { "type": "date" }  
  }}  
}}}

## **9.3 — agent\_events**

PUT /agent\_events  
{ "mappings": { "dynamic": "strict", "properties": {  
  "event\_id":   { "type": "keyword" },  
  "tenant\_id":  { "type": "keyword" },  
  "session\_id": { "type": "keyword" },  
  "plan\_id":    { "type": "keyword" },  
  "task\_id":    { "type": "keyword" },  
  "event\_type": { "type": "keyword" },  
  "skill\_id":   { "type": "keyword" },  
  "script\_hash":{ "type": "keyword" },  
  "payload":    { "type": "object", "enabled": false },  
  "ts":         { "type": "date" }  
}}}

# **10\. Monitoring — Four Layers**

Monitoring is first-class, not an afterthought. Every layer emits structured, queryable data. All layers write to agent\_events in OpenSearch. A dedicated monitoring service reads from agent\_events and agent\_plans to power dashboards and alerts.

## **Layer 1 — Orchestrator Trace (per session)**

Every session produces a complete, ordered timeline queryable from agent\_events by session\_id:

session\_id: sess\_456  
  ├── \[10:00:00\] user\_message.received  
  ├── \[10:00:01\] subagent\_a.spawned         task\_id: task\_001  
  ├── \[10:00:03\] plan.received              plan\_id: plan\_abc  steps: 4  
  ├── \[10:00:03\] plan.persisted             status: executing  
  ├── \[10:00:04\] step\_1.started             task\_id: task\_002  skill: outlet\_list  
  ├── \[10:00:05\] skill.called               skill\_id: outlet\_list  response\_bytes: 1200000  
  ├── \[10:00:05\] large\_response.detected    file\_id: tmp\_xyz  strategy: python\_extract  
  ├── \[10:00:06\] python\_script.executed     script\_hash: a3f7  input\_rows: 4200  output\_rows: 4200  
  ├── \[10:00:07\] memory.written             key: sess\_456:task\_002:outlet\_ids  validated: true  
  ├── \[10:00:07\] step\_1.complete  
  ├── \[10:00:07\] step\_2.started             task\_id: task\_003  skill: analytics  
  ├── \[10:00:09\] step\_2.failed              reason: skill\_timeout  
  ├── \[10:00:09\] replan.triggered           attempt: 1  failed\_step: 2  
  ├── \[10:00:10\] subagent\_a.spawned         task\_id: task\_004  mode: replan  
  ├── \[10:00:12\] plan.revised               revised\_steps: \[2, 3\]  
  └── ...

## **Layer 2 — SubAgent Internals (per task)**

Each subagent emits granular structured events to agent\_events. Every step of the subagent's internal loop is observable:

| Event Type | Key Fields | When Emitted |
| :---- | :---- | :---- |
| skill.called | skill\_id, input\_size\_bytes, response\_size\_bytes, duration\_ms | Every MCP skill invocation |
| large\_response.detected | file\_id, size\_bytes, threshold\_bytes, strategy | Content-length gate triggered |
| python\_script.executed | script\_hash, input\_rows, output\_rows, duration\_ms, exit\_code | exec\_python completed |
| memory.written | key, size\_bytes, validated, lock\_wait\_ms | write\_memory completed |
| contract\_violation | expected\_shape, actual\_shape, step\_index | write\_memory schema check failed |
| subagent.insufficient | reason, suggestion | SubAgent-B cannot complete step |
| subagent.finish | status, duration\_ms, memory\_key | SubAgent-B task complete |

## **Layer 3 — Skill Call Audit (per invocation)**

Every skill call is written to agent\_events with full context. This layer feeds compliance, billing, and optimisation:

* skill\_id, tenant\_id, user\_id, session\_id, plan\_id, task\_id — full lineage

* input\_size\_bytes, response\_size\_bytes — identifies skills that routinely return large payloads

* duration\_ms — identifies slow skills that may need timeout tuning

* large\_response\_triggered: bool — aggregate to find skills needing output contracts

* Queryable: 'show me all skill calls for tenant X in the last 7 days that triggered large response handling'

## **Layer 4 — Plan Health Dashboard**

Aggregate view across all sessions. Powered by queries against agent\_plans and agent\_events:

| Metric | Definition | Actionable When |
| :---- | :---- | :---- |
| Plan success rate | % of plans that reach status: complete without replanning | Drops → planner quality degrading |
| Avg replan count | Mean replans per plan by task type | High → skill schemas not matching return\_specs |
| Step failure rate by skill | Which skill causes most step failures | Identifies unreliable skills |
| Large response frequency | % of skill calls triggering content-length gate | Informs which skills need output filtering at source |
| SubAgent p95 duration | 95th percentile execution time per subagent type | Performance baseline and regression detection |
| Contract violation rate | % of write\_memory calls failing schema check | High → planner generating bad return\_specs |
| Replan exhaustion rate | % of sessions hitting 3-replan limit | Identifies task types the system cannot handle |

## **Alerting**

* Plan success rate drops below 80% over 1h window → page on-call

* Replan exhaustion rate exceeds 5% → investigate planner prompt quality

* Any contract\_violation event → log \+ alert (should be rare if planner verifies against skill schemas)

* exec\_python timeout \> 5% of calls → review extraction script generation

* Memory lock wait \> 2s → investigate concurrent session patterns

# **11\. System Boundaries & API Contract**

## **11.1 Boundary map**

| Zone | Responsibility |
| :---- | :---- |
| Zone A — Chat API | Owns user sessions, auth, streaming to end users. Calls Agentic Service via internal HTTP/gRPC. Passes X-Tenant-Id, X-User-Id, X-Session-Id headers. Never touches OpenSearch or Skill Service directly. |
| Zone B — Agentic Service | Owns AgentCore, PromptBuilder, SubagentManager, ToolCoordinator. Stateless compute. All persistence via Storage Adapter interface only. |
| Zone C — Storage Adapter | Thin translation layer. Converts abstract store calls to OpenSearch index operations. Enforces schema on every write via JSON Schema validation before calling OpenSearch. |
| Zone D — OpenSearch | Five indices. Strict mappings. KNN on agent\_memory. ILM on agent\_events. Field-level security for tenant isolation as additional guard. |
| Zone E — Skill Service | Existing service. MCP \+ HTTP. Access scoped by cloud\_skill\_service env var. Agentic Service is a caller only — never executes skill logic. |
| Zone F — Message Bus | Redis Streams. Carries: subagent lifecycle events, plan step signals, cancellation tokens, memory upsert confirmations. |

## **11.2 Agentic Service API (internal only)**

| Endpoint | Purpose |
| :---- | :---- |
| POST /agent/run | Single agent turn. Body: { tenant\_id, user\_id, session\_id, message, stream }. Returns: { response, plan\_id } or SSE stream. Orchestrator handles all subagent spawning internally — Chat API does not spawn subagents. |
| GET /agent/plans/{plan\_id} | Returns full plan object with step statuses, replan history, memory keys. |
| GET /agent/plans/{plan\_id}/trace | Returns ordered agent\_events for the session — full execution trace. |
| PUT /agent/souls/{tenant\_id} | Upsert soul/persona config. Body: { user\_id?, persona, policies }. |
| GET /agent/memory/query | Semantic memory query. Body: { tenant\_id, user\_id, query\_text, top\_k, scope }. |

| NOTE | There is no POST /agent/subagents endpoint. SubAgent spawning is entirely internal to AgentCore. Chat API calls /agent/run and receives results. It does not manage subagent lifecycle. |
| :---: | :---- |

## **11.3 Identity headers**

X-Tenant-Id:  brand\_123  
X-User-Id:    user\_abc  
X-Session-Id: sess\_456  
X-Api-Key:    \<forwarded to Skill Service calls — never logged\>

# **12\. Acceptance Checklist**

## **Core flow**

* Orchestrator receives a user message, spawns SubAgent-A, receives a plan, iterates steps via SubAgent-B instances, synthesises a final response

* SubAgent-A calls find\_relevant\_skills and load\_skill via MCP, reranks, runs verification pass, returns plan with return\_specs

* SubAgent-B executes one step, handles large responses via write\_temp → read\_lines → exec\_python, validates output against return\_spec before write\_memory

## **Boundaries**

* Chat API is replaced without touching Agentic Service code

* Storage Adapter is swapped to a different backend without touching AgentCore code

* SubAgent-B cannot call skills outside its allowed\_skills list

* SubAgent-B cannot spawn another SubAgent — attempting to do so returns an error

## **Memory & contracts**

* Two concurrent sessions writing to the same user memory do not collide — locking prevents race

* write\_memory rejects output that does not match return\_spec — Orchestrator treats this as step failure

* Memory keys are always namespaced — no manual key construction by SubAgents

## **Replanning**

* Step failure triggers replan — SubAgent-A receives completed steps \+ failure context

* Revised plan preserves completed steps and only replans remaining work

* After 3 replan attempts, Orchestrator returns structured failure — does not loop forever

## **Monitoring**

* Full session trace queryable from agent\_events by session\_id

* Every skill call logged with tenant, size, duration

* Every large-response handling event logged with strategy and script hash

* Plan health dashboard shows success rate, replan count, step failure by skill, contract violation rate

* Alert fires when plan success rate drops below 80%

## **OpenSearch**

* All five indices created with dynamic: strict mappings

* Storage Adapter rejects any write that fails local JSON Schema validation before reaching OpenSearch

* agent\_memory KNN queries pre-filter by tenant\_id \+ scope using bool wrapper

* agent\_events ILM policy configured for retention

*Agentic Service Architecture Blueprint  ·  v3.0  ·  Manager–Planner–Executor–Infrastructure*