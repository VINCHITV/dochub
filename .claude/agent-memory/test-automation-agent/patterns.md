# DocHub Test Patterns

## instructor + AsyncAnthropic Call Signature

In `services/ai.py`, `generate_section()` calls:
```python
pydantic_model, completion = await instructor_client.messages.create_with_completion(
    model=GENERATOR_MODEL,
    max_tokens=4096,
    max_retries=3,
    messages=[{"role": "user", "content": user_prompt}],
    system=system_prompt,
    response_model=section_model,
)
```
To mock, monkeypatch `app.services.ai.instructor_client.messages.create_with_completion`
and return `(pydantic_model_instance, mock_completion_with_usage)`.

## OpenAI Embedding Call

`services/rag.py` uses synchronous `openai.OpenAI()` client:
```python
client = openai.OpenAI()
response = client.embeddings.create(input=query, model=EMBEDDING_MODEL)
return response.data[0].embedding
```
Mock: patch `openai.OpenAI` to return a mock client whose `.embeddings.create()` returns
a response with `data[0].embedding = [0.0] * 1536`.

## ChromaDB Ephemeral Client

For tests, replace `get_chroma_client()` with `chromadb.EphemeralClient()`:
```python
import chromadb
@pytest.fixture
def ephemeral_collection():
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection("test_prd_kb", metadata={"hnsw:space": "cosine"})
```
Never use `PersistentClient` in tests (writes to disk, not isolated).

## RAG: save_prd_to_kb Requires Both OpenAI + ChromaDB

`save_prd_to_kb()` calls `openai.OpenAI().embeddings.create()` internally.
Tests of `save_prd_to_kb` must mock BOTH the OpenAI client AND use ephemeral ChromaDB.
The mock embedding must be a list of 1536 floats.

## Jira httpx Pattern (for future test_jira.py)

The Jira route uses raw `httpx.AsyncClient`. Mock with `respx`:
```python
import respx, httpx

@respx.mock
async def test_create_ticket():
    respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"id": "123", "key": "TEST-1"})
    )
    ...
```

## SSE Stream Parsing Pattern

SSE events from FastAPI generators have format: `data: {json}\n\n`.
To parse in tests with TestClient:
```python
response = client.post("/generate/prd", ...)
events = []
for line in response.iter_lines():
    if line.startswith("data: "):
        events.append(json.loads(line[6:]))
section_events = [e for e in events if "section" in e]
```
Filter out `{"heartbeat": true}` events before counting sections.
