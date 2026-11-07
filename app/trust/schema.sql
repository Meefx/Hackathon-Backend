CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS trust;

CREATE TABLE IF NOT EXISTS trust.audit_events (
    seq bigint PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    occurred_at timestamptz NOT NULL,
    actor_id text NOT NULL,
    actor_role text NOT NULL,
    principal text NOT NULL,
    product text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    prev_hash text NOT NULL CHECK (length(prev_hash) = 64),
    hash text NOT NULL CHECK (length(hash) = 64)
);

CREATE OR REPLACE FUNCTION trust.block_audit_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$;
DROP TRIGGER IF EXISTS audit_no_update_delete ON trust.audit_events;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON trust.audit_events
FOR EACH ROW EXECUTE FUNCTION trust.block_audit_mutation();
DROP TRIGGER IF EXISTS audit_no_truncate ON trust.audit_events;
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON trust.audit_events
FOR EACH STATEMENT EXECUTE FUNCTION trust.block_audit_mutation();

CREATE TABLE IF NOT EXISTS trust.knowledge_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal text NOT NULL,
    product text NOT NULL,
    title text NOT NULL,
    source_uri text NOT NULL,
    source_class text NOT NULL CHECK (source_class IN ('synthetic', 'primary', 'guidance')),
    source_version text NOT NULL,
    source_locator text NOT NULL,
    review_status text NOT NULL CHECK (review_status IN ('synthetic', 'verified', 'review_required')),
    criticality text NOT NULL CHECK (criticality IN ('standard', 'critical')),
    access_roles text[] NOT NULL CHECK (cardinality(access_roles) > 0),
    effective_from timestamptz NOT NULL,
    effective_until timestamptz,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'retired')),
    text text NOT NULL,
    content_hash text NOT NULL CHECK (length(content_hash) = 64),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    qa_by text,
    qa_hash text,
    qa_at timestamptz,
    approved_by text,
    approved_hash text,
    approved_at timestamptz,
    retired_at timestamptz,
    UNIQUE (id, principal, product),
    CHECK (effective_until IS NULL OR effective_until > effective_from),
    CHECK (source_class <> 'synthetic' OR review_status = 'synthetic'),
    CHECK (source_class = 'synthetic' OR review_status <> 'synthetic'),
    CHECK (status <> 'approved' OR (
        qa_by IS NOT NULL AND approved_by IS NOT NULL AND qa_by <> approved_by
        AND qa_hash IS NOT NULL AND approved_hash IS NOT NULL
        AND qa_hash = content_hash AND approved_hash = content_hash
        AND review_status IN ('verified', 'synthetic')
    ))
);

CREATE TABLE IF NOT EXISTS trust.knowledge_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES trust.knowledge_documents(id),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    locator text NOT NULL,
    parent_locator text,
    text text NOT NULL,
    content_hash text NOT NULL CHECK (length(content_hash) = 64),
    embedding vector(64) NOT NULL,
    embedding_profile text NOT NULL DEFAULT 'local_hash_v1_nonsemantic',
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_search ON trust.knowledge_chunks USING gin(search_vector);
CREATE INDEX IF NOT EXISTS knowledge_chunks_vector ON trust.knowledge_chunks
USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS knowledge_documents_scope ON trust.knowledge_documents
(principal, product, status, effective_from);

CREATE TABLE IF NOT EXISTS trust.templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal text NOT NULL,
    product text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    content_hash text NOT NULL CHECK (length(content_hash) = 64),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'suspended')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    qa_by text,
    qa_hash text,
    approved_by text,
    approved_hash text,
    approved_at timestamptz,
    suspended_at timestamptz,
    UNIQUE (id, principal, product),
    CHECK (status <> 'approved' OR (
        qa_by IS NOT NULL AND approved_by IS NOT NULL AND qa_by <> approved_by
        AND qa_hash IS NOT NULL AND approved_hash IS NOT NULL
        AND qa_hash = content_hash AND approved_hash = content_hash
    ))
);

CREATE TABLE IF NOT EXISTS trust.knowledge_dependencies (
    document_id uuid NOT NULL,
    template_id uuid NOT NULL,
    principal text NOT NULL,
    product text NOT NULL,
    relation text NOT NULL DEFAULT 'supports' CHECK (relation = 'supports'),
    weight double precision NOT NULL DEFAULT 1 CHECK (weight > 0 AND weight <= 1),
    PRIMARY KEY (document_id, template_id),
    FOREIGN KEY (document_id, principal, product)
        REFERENCES trust.knowledge_documents(id, principal, product),
    FOREIGN KEY (template_id, principal, product)
        REFERENCES trust.templates(id, principal, product)
);
CREATE INDEX IF NOT EXISTS knowledge_dependencies_template ON trust.knowledge_dependencies(template_id);

CREATE OR REPLACE FUNCTION trust.protect_knowledge_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.principal, NEW.product, NEW.title, NEW.source_uri, NEW.source_class,
        NEW.source_version, NEW.source_locator, NEW.review_status, NEW.criticality,
        NEW.access_roles, NEW.effective_from, NEW.effective_until, NEW.text, NEW.content_hash)
        IS DISTINCT FROM
       (OLD.principal, OLD.product, OLD.title, OLD.source_uri, OLD.source_class,
        OLD.source_version, OLD.source_locator, OLD.review_status, OLD.criticality,
        OLD.access_roles, OLD.effective_from, OLD.effective_until, OLD.text, OLD.content_hash) THEN
        RAISE EXCEPTION 'knowledge versions are immutable; ingest a new version';
    END IF;
    IF OLD.status = 'retired' THEN
        RAISE EXCEPTION 'retired knowledge cannot be reactivated';
    END IF;
    IF OLD.status = 'approved' AND NEW.status <> 'retired' THEN
        RAISE EXCEPTION 'approved knowledge can only be retired';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS knowledge_immutable ON trust.knowledge_documents;
CREATE TRIGGER knowledge_immutable BEFORE UPDATE ON trust.knowledge_documents
FOR EACH ROW EXECUTE FUNCTION trust.protect_knowledge_version();

CREATE OR REPLACE FUNCTION trust.protect_template_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.principal, NEW.product, NEW.body, NEW.content_hash, NEW.title)
        IS DISTINCT FROM (OLD.principal, OLD.product, OLD.body, OLD.content_hash, OLD.title) THEN
        RAISE EXCEPTION 'template versions are immutable; create a new version';
    END IF;
    IF OLD.status = 'suspended' OR (OLD.status = 'approved' AND NEW.status <> 'suspended') THEN
        RAISE EXCEPTION 'approved templates can only be suspended';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS templates_immutable ON trust.templates;
CREATE TRIGGER templates_immutable BEFORE UPDATE ON trust.templates
FOR EACH ROW EXECUTE FUNCTION trust.protect_template_version();

CREATE OR REPLACE FUNCTION trust.protect_dependency() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE current_status text; reviewed_by text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'dependency edges cannot be rewritten';
    END IF;
    SELECT status,qa_by INTO current_status,reviewed_by FROM trust.templates
      WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.template_id ELSE NEW.template_id END;
    IF current_status <> 'draft' OR reviewed_by IS NOT NULL THEN
        RAISE EXCEPTION 'reviewed template dependencies are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS dependencies_immutable ON trust.knowledge_dependencies;
CREATE TRIGGER dependencies_immutable BEFORE INSERT OR UPDATE OR DELETE
ON trust.knowledge_dependencies FOR EACH ROW EXECUTE FUNCTION trust.protect_dependency();

CREATE OR REPLACE FUNCTION trust.protect_chunk() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'knowledge chunks are immutable';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM trust.knowledge_documents
        WHERE id=NEW.document_id AND status='draft' AND qa_by IS NULL) THEN
        RAISE EXCEPTION 'chunks can only be added to an unreviewed draft';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS chunks_immutable ON trust.knowledge_chunks;
CREATE TRIGGER chunks_immutable BEFORE INSERT OR UPDATE OR DELETE ON trust.knowledge_chunks
FOR EACH ROW EXECUTE FUNCTION trust.protect_chunk();
