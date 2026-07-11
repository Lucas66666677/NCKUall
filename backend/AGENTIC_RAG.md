# Agentic RAG Router

`POST /api/chat` now routes retrieval through an agentic orchestration layer:

1. `route_intent()` classifies the user query into:
   - `course`
   - `career`
   - `life`
   - `composite`
2. The selected tools run asynchronously with independent SQLAlchemy
   `AsyncSession` objects:
   - `CourseTool`
   - `CareerTool`
   - `LifeTool`
3. Tool results are deduplicated, reranked, and passed to the final answer
   model as grounded context.
4. If no tool returns context, the system immediately returns:

   `我不知道。依照目前可用的資料庫與工具，沒有找到足夠且符合科系/分類條件的真實資料；我不會用猜測補答案。`

   The LLM is not called in this case.

## Router Mode

```bash
RAG_AGENT_ROUTER_MODE=llm
```

`llm` asks the configured lightweight chat model to return a JSON routing
decision. If the router call fails, it falls back to deterministic keyword
routing.

```bash
RAG_AGENT_ROUTER_MODE=keyword
```

Use this mode for local testing or cost-sensitive deployments.

## Tool Isolation

Each tool receives the shared `async_sessionmaker` and opens its own
`AsyncSession`. This is intentional: one SQLAlchemy `AsyncSession` must not be
used concurrently by several coroutines.

## Hallucination Boundary

The final generation prompt forbids using model memory as source material. The
controller only invokes the answer model when at least one tool produced
grounding context and citations.
