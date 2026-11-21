-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS org;

CREATE TABLE org.institution (
	institution_code VARCHAR(20) NOT NULL, 
	government_code VARCHAR(40), 
	name VARCHAR(200) NOT NULL, 
	name_key VARCHAR(200) NOT NULL, 
	lga_scope_id BINARY(16) NOT NULL, 
	ownership VARCHAR(8) NOT NULL, 
	managing_body VARCHAR(64), 
	status VARCHAR(8) NOT NULL, 
	town VARCHAR(100), 
	address VARCHAR(255), 
	phone VARCHAR(20), 
	email VARCHAR(254), 
	latitude NUMERIC(9, 6), 
	longitude NUMERIC(9, 6), 
	head_person_id BINARY(16), 
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
	CONSTRAINT pk_institution PRIMARY KEY (id), 
	CONSTRAINT uq_institution_tenant_id_institution_code UNIQUE (tenant_id, institution_code), 
	CONSTRAINT uq_institution_tenant_id_government_code UNIQUE (tenant_id, government_code), 
	CONSTRAINT uq_institution_tenant_id_lga_scope_id_name_key UNIQUE (tenant_id, lga_scope_id, name_key), 
	CONSTRAINT ck_institution_ownership CHECK (ownership IN ('public','private')), 
	CONSTRAINT ck_institution_status CHECK (status IN ('active','closed')), 
	CONSTRAINT fk_institution_head_person_id_person FOREIGN KEY(head_person_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_institution_tenant_id_lga_scope_id ON org.institution (tenant_id, lga_scope_id);

CREATE TABLE org.institution_level (
	institution_id BINARY(16) NOT NULL, 
	level VARCHAR(8) NOT NULL, 
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
	CONSTRAINT pk_institution_level PRIMARY KEY (id), 
	CONSTRAINT uq_institution_level_tenant_id_institution_id_level UNIQUE (tenant_id, institution_id, level), 
	CONSTRAINT ck_institution_level_level CHECK (level IN ('eccde','primary','jss','sss')), 
	CONSTRAINT fk_institution_level_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
