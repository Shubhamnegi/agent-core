from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.domain.exceptions import StorageSchemaError
from agent_core.domain.models import EventRecord
from agent_core.infra.adapters.opensearch import (
    OpenSearchEventRepository,
    OpenSearchIndexManager,
    OpenSearchMemoryRepository,
    build_agent_memory_knn_query,
)
from agent_core.infra.adapters.opensearch_schemas import (
    ALL_INDEXES,
    EVENTS_ILM_POLICY,
    INDEX_AGENT_EVENTS,
    INDEX_AGENT_MEMORY,
    build_index_definition,
    validate_document_schema,
)


class _FakeIndicesClient:
    def __init__(self) -> None:
        self.created: dict[str, dict] = {}

    def exists(self, index: str) -> bool:
        return index in self.created

    def create(self, index: str, body: dict) -> None:
        self.created[index] = body


class _FakeIlmClient:
    def __init__(self) -> None:
        self.policies: dict[str, dict] = {}

    def put_policy(self, policy: str, body: dict) -> None:
        self.policies[policy] = body


class _FakeTransportClient:
    def __init__(self, ilm: _FakeIlmClient) -> None:
        self._ilm = ilm

    def perform_request(self, method: str, path: str, body: dict) -> None:
        assert method == "PUT"
        prefix = "/_plugins/_ism/policies/"
        assert path.startswith(prefix)
        policy_id = path.removeprefix(prefix)
        self._ilm.put_policy(policy_id, body)


class _FakeOpenSearchClient:
    def __init__(self) -> None:
        self.indices = _FakeIndicesClient()
        self.ilm = _FakeIlmClient()
        self.transport = _FakeTransportClient(self.ilm)
        self.docs: dict[str, dict[str, dict]] = {}
        self.last_search_query: dict | None = None

    def index(self, index: str, id: str, body: dict, refresh: str) -> None:
        _ = refresh
        self.docs.setdefault(index, {})[id] = body

    def get(self, index: str, id: str, ignore: list[int] | None = None) -> dict:
        _ = ignore
        bucket = self.docs.get(index, {})
        if id not in bucket:
            return {"found": False}
        return {"found": True, "_source": bucket[id]}

    def search(self, index: str, body: dict) -> dict:
        self.last_search_query = body
        bucket = self.docs.get(index, {})
        if "term" in body.get("query", {}):
            plan_id = body["query"]["term"].get("plan_id")
            hits = [
                {"_source": value}
                for value in bucket.values()
                if value.get("plan_id") == plan_id
            ]
            return {"hits": {"hits": hits}}
        return {"hits": {"hits": []}}


class _FakeEmbeddingService:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed_text(self, text: str) -> list[float]:
        _ = text
        return self.vector


def test_section_g_index_manager_creates_all_indices_with_strict_mappings_and_ilm() -> None:
    client = _FakeOpenSearchClient()
    manager = OpenSearchIndexManager(client=client, index_prefix="", embedding_dims=256)

    manager.ensure_indices_and_policies()

    assert EVENTS_ILM_POLICY in client.ilm.policies
    assert set(client.indices.created.keys()) == set(ALL_INDEXES)

    for index_name, definition in client.indices.created.items():
        assert definition["mappings"]["dynamic"] == "strict"
        if index_name == INDEX_AGENT_MEMORY:
            assert definition["settings"]["index"]["knn"] is True
            assert definition["mappings"]["properties"]["embedding"]["dimension"] == 256
        if index_name == INDEX_AGENT_EVENTS:
            assert (
                definition["settings"]["index"]["plugins.index_state_management.policy_id"]
                == EVENTS_ILM_POLICY
            )


def test_section_g_local_schema_validation_raises_storage_schema_error() -> None:
    # Why this case: missing required fields is the most common integration failure mode.
    invalid_event = {
        "event_type": "plan.persisted",
        "tenant_id": "tenant-1",
    }

    with pytest.raises(StorageSchemaError, match="missing required field"):
        validate_document_schema(INDEX_AGENT_EVENTS, invalid_event)


def test_section_g_knn_query_applies_tenant_scope_prefilter() -> None:
    query = build_agent_memory_knn_query(
        tenant_id="tenant-1",
        scope="session",
        query_vector=[0.1, 0.2, 0.3],
        top_k=5,
    )

    filters = query["query"]["bool"]["filter"]
    assert {"term": {"tenant_id": "tenant-1"}} in filters
    assert {"term": {"scope": "session"}} in filters


@pytest.mark.asyncio
async def test_section_g_event_repository_validates_and_persists_event_documents() -> None:
    client = _FakeOpenSearchClient()
    index_name = "agent_events"
    client.indices.create(index=index_name, body=build_index_definition(index_name))
    repo = OpenSearchEventRepository(client=client)

    event = EventRecord(
        event_type="plan.persisted",
        tenant_id="tenant-1",
        session_id="session-1",
        plan_id="plan-1",
        task_id=None,
        payload={"steps": 2},
        ts=datetime.now(UTC),
    )

    await repo.append(event)
    events = await repo.list_by_plan("plan-1")

    assert len(events) == 1
    assert events[0].event_type == "plan.persisted"


@pytest.mark.asyncio
async def test_section_g_memory_repository_rejects_contract_mismatch_before_indexing() -> None:
    client = _FakeOpenSearchClient()
    index_name = "agent_memory"
    client.indices.create(index=index_name, body=build_index_definition(index_name))
    repo = OpenSearchMemoryRepository(client=client)

    with pytest.raises(Exception, match="contract_violation"):
        await repo.write(
            tenant_id="tenant-1",
            session_id="session-1",
            task_id="task-1",
            key="summary",
            value={"total": "should-be-integer"},
            return_spec_shape={"total": "integer"},
        )


@pytest.mark.asyncio
async def test_section_g_memory_repository_writes_embedding_before_indexing() -> None:
    client = _FakeOpenSearchClient()
    index_name = "agent_memory"
    client.indices.create(index=index_name, body=build_index_definition(index_name))
    repo = OpenSearchMemoryRepository(
        client=client,
        embedding_service=_FakeEmbeddingService([0.1, 0.2, 0.3]),
        expected_embedding_dims=3,
    )

    key = await repo.write(
        tenant_id="tenant-1",
        session_id="session-1",
        task_id="task-1",
        key="summary",
        value={"total": 42},
        return_spec_shape={"total": "integer"},
    )

    stored = client.docs[index_name][key]
    assert stored["embedding"] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_section_g_memory_repository_detects_embedding_dimension_mismatch() -> None:
    client = _FakeOpenSearchClient()
    index_name = "agent_memory"
    client.indices.create(index=index_name, body=build_index_definition(index_name))
    repo = OpenSearchMemoryRepository(
        client=client,
        embedding_service=_FakeEmbeddingService([0.1, 0.2]),
        expected_embedding_dims=3,
    )

    with pytest.raises(RuntimeError, match="embedding_dimension_mismatch"):
        await repo.write(
            tenant_id="tenant-1",
            session_id="session-1",
            task_id="task-1",
            key="summary",
            value={"total": 42},
            return_spec_shape={"total": "integer"},
        )
