-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS gov;

CREATE TABLE gov.config_setting (
	setting_key VARCHAR(96) NOT NULL, 
	value JSON NOT NULL, 
	locked_below BOOL NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BINARY(16), 
	updated_by BINARY(16), 
	row_version INTEGER NOT NULL, 
	source_system VARCHAR(64), 
	source_ref VARCHAR(128), 
	id BINARY(16) NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_to DATE, 
	CONSTRAINT pk_config_setting PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_config_setting_tenant_id_scope_node_id_setting_key_valid_from ON gov.config_setting (tenant_id, scope_node_id, setting_key, valid_from);

CREATE TABLE gov.tenant (
	id BINARY(16) NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	parent_tenant_id BINARY(16), 
	root_scope_node_id BINARY(16), 
	country VARCHAR(2), 
	residency_region VARCHAR(32) NOT NULL, 
	ownership VARCHAR(16) NOT NULL, 
	time_zone VARCHAR(64) NOT NULL, 
	default_locale VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_tenant PRIMARY KEY (id), 
	CONSTRAINT ck_tenant_kind CHECK (kind IN ('global','country','regional','network','institutional')), 
	CONSTRAINT uq_tenant_code UNIQUE (code), 
	CONSTRAINT fk_tenant_parent_tenant_id_tenant FOREIGN KEY(parent_tenant_id) REFERENCES gov.tenant (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE gov.scope_node (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	parent_id BINARY(16), 
	level INTEGER NOT NULL, 
	code VARCHAR(64) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	local_label VARCHAR(64), 
	path VARCHAR(512) NOT NULL, 
	is_tenant_root BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_scope_node PRIMARY KEY (id), 
	CONSTRAINT uq_scope_node_tenant_id_code UNIQUE (tenant_id, code), 
	CONSTRAINT ck_scope_node_level CHECK (level BETWEEN 1 AND 9), 
	CONSTRAINT fk_scope_node_tenant_id_tenant FOREIGN KEY(tenant_id) REFERENCES gov.tenant (id), 
	CONSTRAINT fk_scope_node_parent_id_scope_node FOREIGN KEY(parent_id) REFERENCES gov.scope_node (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_scope_node_tenant_id_path ON gov.scope_node (tenant_id, path);

CREATE TABLE gov.policy_rule (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	owner_scope_id BINARY(16) NOT NULL, 
	rule_key VARCHAR(32) NOT NULL, 
	domain VARCHAR(32) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	expression TEXT NOT NULL, 
	parameters JSON NOT NULL, 
	strictness JSON NOT NULL, 
	test_cases JSON NOT NULL, 
	mandatory_below BOOL NOT NULL, 
	version INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_to DATE, 
	published_at DATETIME(6), 
	published_by BINARY(16), 
	CONSTRAINT pk_policy_rule PRIMARY KEY (id), 
	CONSTRAINT uq_policy_rule_tenant_id_owner_scope_id_rule_key_version UNIQUE (tenant_id, owner_scope_id, rule_key, version), 
	CONSTRAINT ck_policy_rule_status CHECK (status IN ('draft','published','withdrawn','superseded')), 
	CONSTRAINT fk_policy_rule_owner_scope_id_scope_node FOREIGN KEY(owner_scope_id) REFERENCES gov.scope_node (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE gov.scope_closure (
	ancestor_id BINARY(16) NOT NULL, 
	descendant_id BINARY(16) NOT NULL, 
	depth INTEGER NOT NULL, 
	CONSTRAINT pk_scope_closure PRIMARY KEY (ancestor_id, descendant_id), 
	CONSTRAINT fk_scope_closure_ancestor_id_scope_node FOREIGN KEY(ancestor_id) REFERENCES gov.scope_node (id), 
	CONSTRAINT fk_scope_closure_descendant_id_scope_node FOREIGN KEY(descendant_id) REFERENCES gov.scope_node (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_scope_closure_descendant_id_ancestor_id ON gov.scope_closure (descendant_id, ancestor_id);
