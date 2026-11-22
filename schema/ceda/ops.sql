-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS ops;

CREATE TABLE ops.audit_chain_head (
	tenant_id BINARY(16) NOT NULL, 
	chain_date DATE NOT NULL, 
	last_seq BIGINT NOT NULL, 
	last_hash BINARY(32) NOT NULL, 
	closed_at DATETIME(6), 
	anchored_s3_key VARCHAR(512), 
	CONSTRAINT pk_audit_chain_head PRIMARY KEY (tenant_id, chain_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops.audit_event (
	id BINARY(16) NOT NULL, 
	chain_date DATE NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	seq BIGINT NOT NULL, 
	occurred_at DATETIME(6) NOT NULL, 
	actor_id BINARY(16), 
	on_behalf_of BINARY(16), 
	action VARCHAR(64) NOT NULL, 
	entity_type VARCHAR(64) NOT NULL, 
	entity_id BINARY(16), 
	scope_node_id BINARY(16), 
	purpose VARCHAR(64), 
	before_hash BINARY(32), 
	after_hash BINARY(32), 
	prev_hash BINARY(32) NOT NULL, 
	event_hash BINARY(32) NOT NULL, 
	ip VARCHAR(45), 
	device_id VARCHAR(128), 
	correlation_id BINARY(16), 
	changed_fields JSON, 
	CONSTRAINT pk_audit_event PRIMARY KEY (id, chain_date), 
	CONSTRAINT uq_audit_event_tenant_id_chain_date_seq UNIQUE (tenant_id, chain_date, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_audit_event_tenant_id_actor_id_occurred_at ON ops.audit_event (tenant_id, actor_id, occurred_at);

CREATE INDEX ix_audit_event_tenant_id_entity_type_entity_id ON ops.audit_event (tenant_id, entity_type, entity_id);

CREATE TABLE ops.bulk_job (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	source_name VARCHAR(255), 
	summary JSON NOT NULL, 
	created_by BINARY(16), 
	created_at DATETIME(6) NOT NULL, 
	committed_by BINARY(16), 
	committed_at DATETIME(6), 
	CONSTRAINT pk_bulk_job PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_bulk_job_tenant_id_created_at ON ops.bulk_job (tenant_id, created_at);

CREATE TABLE ops.id_sequence (
	tenant_id BINARY(16) NOT NULL, 
	name VARCHAR(32) NOT NULL, 
	`last_value` BIGINT NOT NULL, 
	CONSTRAINT pk_id_sequence PRIMARY KEY (tenant_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops.idempotency_record (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	idem_key VARCHAR(128) NOT NULL, 
	request_hash BINARY(32) NOT NULL, 
	status_code INTEGER NOT NULL, 
	response_body BLOB NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_idempotency_record PRIMARY KEY (id), 
	CONSTRAINT uq_idempotency_record_tenant_id_user_id_idem_key UNIQUE (tenant_id, user_id, idem_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops.outbound_email (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	purpose VARCHAR(48) NOT NULL, 
	to_address VARCHAR(254) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	sent_at DATETIME(6), 
	attempts INTEGER NOT NULL, 
	last_error VARCHAR(500), 
	CONSTRAINT pk_outbound_email PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_outbound_email_sent_at_id ON ops.outbound_email (sent_at, id);

CREATE TABLE ops.outbox_event (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	aggregate_type VARCHAR(64) NOT NULL, 
	aggregate_id BINARY(16) NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	topic VARCHAR(128) NOT NULL, 
	payload JSON NOT NULL, 
	schema_version INTEGER NOT NULL, 
	actor_id BINARY(16), 
	correlation_id BINARY(16), 
	occurred_at DATETIME(6) NOT NULL, 
	published_at DATETIME(6), 
	attempts INTEGER NOT NULL, 
	last_error TEXT, 
	CONSTRAINT pk_outbox_event PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_outbox_event_aggregate_type_aggregate_id ON ops.outbox_event (aggregate_type, aggregate_id);

CREATE INDEX ix_outbox_event_published_at_id ON ops.outbox_event (published_at, id);

CREATE TABLE ops.bulk_job_row (
	job_id BINARY(16) NOT NULL, 
	row_no INTEGER NOT NULL, 
	data JSON NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	messages JSON NOT NULL, 
	result_id BINARY(16), 
	CONSTRAINT pk_bulk_job_row PRIMARY KEY (job_id, row_no), 
	CONSTRAINT fk_bulk_job_row_job_id_bulk_job FOREIGN KEY(job_id) REFERENCES ops.bulk_job (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
