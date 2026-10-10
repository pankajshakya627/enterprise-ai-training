# MongoDB Atlas Vector Search index

Create this index manually in Atlas (Atlas UI > Atlas Search > Create Search Index > Atlas Vector Search > JSON editor) on the collection named by `MONGODB_DB` / `MONGODB_COLLECTION`.

- **Index name:** `policy_chunks_v1` (must match `VECTOR_INDEX_NAME`)
- **numDimensions:** must match `EMBEDDING_DIMENSIONS` (1536 for `text-embedding-3-small`)

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    { "type": "filter", "path": "product_id" },
    { "type": "filter", "path": "jurisdiction" },
    { "type": "filter", "path": "effective_date" },
    { "type": "filter", "path": "effective_to" },
    { "type": "filter", "path": "doc_type" },
    { "type": "filter", "path": "confidentiality_level" }
  ]
}
```

`effective_date` and `effective_to` together support an as-of query: `effective_date <= d AND (effective_to is null OR effective_to > d)`. Filter fields can only be used in a query if they are in the index, so adding one later means rebuilding the index.

Changing the embedding model or dimension means a new index version (`policy_chunks_v2`) and a full re-embed, never a mixed index.

## Parent sections collection

Whole sections live in a second, plain collection named by `MONGODB_PARENT_COLLECTION` (default `policy_sections`), keyed by `parent_id` (`<doc_id>_s<NN>`) in `_id`. It is not embedded and needs no search index. MongoDB creates it on the first ingest.

## Regular index for stale-chunk removal

After each document, ingestion deletes that document's leftover chunks with a query on `doc_id`. At eight documents a collection scan is fine. At scale, add a regular (not search) index on the chunk collection:

```json
{ "doc_id": 1 }
```
