-- 0001_initial.sql — initial schema for the RCA platform (PoC slice).
-- Run against the QA Postgres:  psql "$DATABASE_URL" -f db/migrations/0001_initial.sql
-- Matches db/models.py. ABAC is enforced by RLS reading the GUCs set in db/engine.apply_scope:
--   app.user_id, app.team_scope (comma-separated team ids), app.is_admin ('true'/'false').
-- The audit_log is WORM: a trigger blocks UPDATE/DELETE.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------------------------------------------------------------- core tables

CREATE TABLE incidents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system     VARCHAR(32)  NOT NULL,
    fingerprint       VARCHAR(256) NOT NULL,
    title             TEXT         NOT NULL,
    description       TEXT,
    provider_severity VARCHAR(16),
    pagerduty_id      VARCHAR(64),
    servicenow_id     VARCHAR(64),
    raw_payload       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    team_id           VARCHAR(64)  NOT NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_incidents_fingerprint ON incidents (fingerprint);
CREATE INDEX ix_incidents_team_id     ON incidents (team_id);

CREATE TABLE investigations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id     UUID         NOT NULL REFERENCES incidents (id),
    status          VARCHAR(32)  NOT NULL DEFAULT 'created',
    severity        VARCHAR(16),
    title           TEXT         NOT NULL,
    team_id         VARCHAR(64)  NOT NULL,
    idempotency_key VARCHAR(128) UNIQUE,
    state           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_investigations_incident_id ON investigations (incident_id);
CREATE INDEX ix_investigations_status      ON investigations (status);
CREATE INDEX ix_investigations_team_id     ON investigations (team_id);
CREATE INDEX ix_investigations_keyset      ON investigations (created_at DESC, id DESC);

CREATE TABLE approvals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id UUID         NOT NULL REFERENCES investigations (id),
    decision         VARCHAR(32)  NOT NULL,   -- approve | reject | needs_changes
    target           VARCHAR(64)  NOT NULL,   -- review_gate | recommendation
    target_id        VARCHAR(128),
    comment          TEXT,
    decided_by       VARCHAR(128) NOT NULL,
    team_id          VARCHAR(64)  NOT NULL,
    decided_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_approvals_investigation_id ON approvals (investigation_id);
CREATE INDEX ix_approvals_team_id          ON approvals (team_id);

CREATE TABLE feedback (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id UUID         NOT NULL REFERENCES investigations (id),
    rating           INTEGER,
    useful           BOOLEAN,
    category         VARCHAR(64),
    comment          TEXT,
    target_id        VARCHAR(128),
    submitted_by     VARCHAR(128) NOT NULL,
    team_id          VARCHAR(64)  NOT NULL,
    submitted_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_feedback_investigation_id ON feedback (investigation_id);
CREATE INDEX ix_feedback_team_id          ON feedback (team_id);

-- Draft-only. The platform never posts these externally.
CREATE TABLE communications (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id UUID         NOT NULL REFERENCES investigations (id),
    channel          VARCHAR(32)  NOT NULL,
    audience         VARCHAR(64),
    status           VARCHAR(16)  NOT NULL DEFAULT 'draft',
    content          TEXT         NOT NULL,
    created_by       VARCHAR(64)  NOT NULL,
    team_id          VARCHAR(64)  NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_communications_investigation_id ON communications (investigation_id);
CREATE INDEX ix_communications_team_id          ON communications (team_id);

-- Append-only, hash-chained audit (Phase 2 §1.10). WORM enforced below.
CREATE TABLE audit_log (
    seq              BIGSERIAL PRIMARY KEY,
    id               UUID         NOT NULL DEFAULT gen_random_uuid(),
    category         VARCHAR(64)  NOT NULL,
    action           VARCHAR(128) NOT NULL,
    actor_id         VARCHAR(128) NOT NULL,
    investigation_id UUID,
    request_id       VARCHAR(128) NOT NULL,
    model_id         VARCHAR(128),
    model_version    VARCHAR(64),
    tool_name        VARCHAR(128),
    tool_params      JSONB,
    result_summary   TEXT,
    audit_metadata   JSONB,
    prev_hash        VARCHAR(64)  NOT NULL,
    this_hash        VARCHAR(64)  NOT NULL UNIQUE,
    occurred_at      TIMESTAMPTZ  NOT NULL
);
CREATE INDEX ix_audit_log_investigation_id ON audit_log (investigation_id);

-- ---------------------------------------------------------------- ABAC / RLS
-- Policies read the request-scoped GUCs set by db/engine.apply_scope. current_setting(.., true)
-- returns NULL when unset (missing_ok), so an unscoped session sees nothing (fail closed).

CREATE OR REPLACE FUNCTION app_in_team_scope(row_team VARCHAR) RETURNS BOOLEAN AS $$
    SELECT coalesce(current_setting('app.is_admin', true) = 'true', false)
        OR row_team = ANY (string_to_array(coalesce(current_setting('app.team_scope', true), ''), ','));
$$ LANGUAGE sql STABLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['incidents','investigations','approvals','feedback','communications']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format($f$
            CREATE POLICY %1$s_team_isolation ON %1$I
            USING (app_in_team_scope(team_id))
            WITH CHECK (app_in_team_scope(team_id));
        $f$, t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------- WORM audit
-- Block any UPDATE/DELETE on audit_log at the database level (defense beyond grants).

CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (WORM): % not permitted', TG_OP;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_mutation
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

-- NOTE: in QA/prod, grant the application role INSERT/SELECT only on audit_log
-- (no UPDATE/DELETE/TRUNCATE), and run the API under a non-superuser, non-BYPASSRLS role
-- so RLS and the WORM trigger are actually enforced.

COMMIT;
