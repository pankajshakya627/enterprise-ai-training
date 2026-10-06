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
    { "type": "filter", "path": "confidentiality_level" }
  ]
}
```

Changing the embedding model or dimension means a new index version (`policy_chunks_v2`) and a full re-embed, never a mixed index.
