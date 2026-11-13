# AI usage and optimization investigation

Reviewed local source on 2026-11-12. The user asked to skip contacting Nishitha;
this document records findings and an approach for later team review, not an
agreement reached with her. No paid model call or embedding request was made.

## Current prompts and budget behavior

`blindspottest/semantic/classifier.py` sends its static instructions, a JSON page
snapshot and a response schema. `flow_classifier.py` sends instructions, a graph
summary and readable element summaries (up to 14 per state). In those inspected
paths, BRD/SRS documents are not sent. This does not establish behavior for
unavailable branches or engines; A1 currently has only a stub engine.

The original budget wrapper already uses an exact-request hash cache, conservative
UTF-8 byte reservations plus output allowance, call/token caps, and blocking of
automatic retry for failed/interrupted calls. It stores local journal responses;
it is not a reliable USD billing ledger. The new metered provider persists per-call
usage revisions and supplied exact amounts, and leaves absent prices/tokens null.
A1 supplies no model adapter, pricing table, prompt cache, or automatic request retry.

Offline measurements of the static SYSTEM_PROMPT constants:

| Prompt | UTF-8 bytes | Character/4 English token heuristic |
|---|---:|---:|
| Page classifier | 3,529 | 879 |
| Flow classifier | 4,775 | 1,189 |

These exclude page/graph data, schemas, images, framing, output, and caching.
They are not measured model tokens. Reproduce without importing the original
application or any model SDK:

```sh
.venv/bin/python tools/prompt_metrics.py ../blindspottest/semantic/classifier.py ../blindspottest/semantic/flow_classifier.py --python-constant SYSTEM_PROMPT
```

Approximate billed USD per request requires actual model/rates:
`uncached_input_tokens*input_rate/1e6 + cached_input_tokens*cache_rate/1e6 + output_tokens*output_rate/1e6`,
plus cache-write/image charges if applicable. Current production usage, model
prices, cache hit rates and workload are unavailable, so total/current savings
remain unknown. Do not turn missing observations into $0.

## Proposed optimization sequence

1. Collect transport-measured input/output/cache tokens, latency, operations,
   exact supplied costs, and verdict quality on a fixed, redacted fixture set.
2. Send the active requirement and its acceptance criteria; do not attach the
   entire requirement corpus to every step. Preserve IDs for evidence mapping.
3. Send bounded relevant page regions and graph/readable summaries, deduplicate
   repeated observations, omit whitespace-only prompt overhead and credentials.
   Preserve negative evidence needed to justify an inconclusive verdict.
4. Keep a stable instructions/schema prefix and changing run context at the end.
   Evaluate model-specific prompt caching only after measuring repeated prefix
   size and hit rate. Cache writes, minimum sizes and lifetimes matter; see
   [Anthropic's prompt caching reference](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
5. Try a less expensive model for applicability/routing on the same fixtures,
   while retaining a stronger verification path if quality falls. Do not pick a
   model by price alone or interpret an inconclusive result as a pass.
6. Compare paired before/after input/output tokens, actual known costs, latency,
   locator success, criterion accuracy and inconclusive rate. No measured
   before/after improvement is claimed yet because no real engine is running.

## Retrieval decision

Proposal: start with ID-based selection of requirements and versioned structured
summaries. Add lexical retrieval if selecting the right requirement becomes
hard. Evaluate embeddings/RAG only if that baseline misses relevant material on
a representative test set. RAG introduces ingestion, versioning, authorization,
embedding refresh, relevance evaluation, latency, and additional cost.

If needed, BE should own a versioned corpus of BRD/SRS requirement chunks and
explicitly approved previous results. Scope every lookup by tenant/project and
immutable document version. Exclude credentials, raw sensitive screenshots and
unverified prior verdicts. A worker can ask BE for relevant chunks before a model
call; an event-driven pipeline can consume a document-indexed event and store
versioned retrieval references. Neither design requires aiservice to query BE's
database directly. Persist chunk IDs/version in the result provenance.

Storage options: PostgreSQL full-text search avoids an additional service for
lexical retrieval. If semantic retrieval proves necessary, BE could add pgvector
to its own database or operate a dedicated vector service. [pgvector supports
exact and approximate search](https://github.com/pgvector/pgvector); additional
indexes trade recall, build time and memory. No vector database or embedding
integration is installed by A1.

Estimate added cost as corpus embedding/re-embedding + retrieval-query embeddings
+ vector storage/compute + extra network + retrieved prompt tokens. Compare that
against saved model tokens and verdict-quality gains. Corpus size, update rate,
model selection and tenant requirements are currently unknown, so an absolute
RAG infrastructure quote would be speculative.
