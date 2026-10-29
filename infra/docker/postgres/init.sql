-- =============================================================================
-- Cybotech — PostgreSQL initialisation script
-- Run once by Docker Compose on first container start.
-- Enables the pgvector extension required for semantic memory retrieval.
-- =============================================================================

-- Enable pgvector (ships pre-installed in pgvector/pgvector:pg16 image)
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_trgm for fuzzy text search (useful for document search)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable uuid-ossp for UUID generation helpers
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
