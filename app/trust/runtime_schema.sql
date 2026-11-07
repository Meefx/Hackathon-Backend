CREATE TABLE IF NOT EXISTS trust.cases (
    id uuid PRIMARY KEY,
    principal text NOT NULL,
    product text NOT NULL,
    borrower_ref uuid NOT NULL,
    is_synthetic boolean NOT NULL CHECK (is_synthetic),
    context jsonb NOT NULL,
    facts jsonb NOT NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL,
    UNIQUE (id, principal, product)
);
CREATE TABLE IF NOT EXISTS trust.contact_budgets (
    principal text NOT NULL,
    product text NOT NULL,
    borrower_ref uuid NOT NULL,
    PRIMARY KEY (principal, product, borrower_ref)
);
CREATE TABLE IF NOT EXISTS trust.attempts (
    id uuid PRIMARY KEY,
    principal text NOT NULL,
    product text NOT NULL,
    case_id uuid NOT NULL,
    template_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash text NOT NULL,
    case_version integer NOT NULL,
    status text NOT NULL CHECK (status IN ('APPROVED','BLOCKED','NOT_SENT','DELIVERED')),
    text_sha256 text,
    evidence jsonb NOT NULL,
    decision jsonb NOT NULL,
    policy_version text NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (principal, product, idempotency_key),
    FOREIGN KEY (case_id,principal,product) REFERENCES trust.cases(id,principal,product)
);
CREATE TABLE IF NOT EXISTS trust.dispatches (
    id uuid PRIMARY KEY,
    attempt_id uuid NOT NULL UNIQUE REFERENCES trust.attempts(id),
    principal text NOT NULL,
    product text NOT NULL,
    case_id uuid NOT NULL,
    borrower_ref uuid NOT NULL,
    text_sha256 text NOT NULL,
    body text NOT NULL,
    purpose text NOT NULL,
    is_simulated boolean NOT NULL CHECK (is_simulated),
    delivered_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS trust_budget_history
    ON trust.dispatches(principal,product,borrower_ref,delivered_at);
CREATE TABLE IF NOT EXISTS trust.conversations (
    id uuid PRIMARY KEY,
    principal text NOT NULL,
    product text NOT NULL,
    case_id uuid NOT NULL,
    body text NOT NULL,
    context_redacted text NOT NULL,
    categories text[] NOT NULL,
    is_synthetic boolean NOT NULL CHECK (is_synthetic),
    created_at timestamptz NOT NULL,
    FOREIGN KEY (case_id,principal,product) REFERENCES trust.cases(id,principal,product)
);
CREATE TABLE IF NOT EXISTS trust.request_records (
    principal text NOT NULL,
    product text NOT NULL,
    key uuid NOT NULL,
    route text NOT NULL,
    actor_id text NOT NULL,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    PRIMARY KEY(principal,product,key,route)
);
