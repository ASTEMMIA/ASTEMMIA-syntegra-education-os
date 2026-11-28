-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS mdm;

CREATE TABLE mdm.address (
	address_no INTEGER NOT NULL, 
	address_line1 VARCHAR(255) NOT NULL, 
	address_line2 VARCHAR(255), 
	address_line3 VARCHAR(255), 
	address_line4 VARCHAR(255), 
	address_line5 VARCHAR(255), 
	local_government_area VARCHAR(64), 
	lga_key VARCHAR(64), 
	state VARCHAR(64), 
	country VARCHAR(64), 
	postcode VARCHAR(16), 
	paf_key VARCHAR(40), 
	address_description VARCHAR(255), 
	address_key BINARY(32) NOT NULL, 
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
	CONSTRAINT pk_address PRIMARY KEY (id), 
	CONSTRAINT uq_address_tenant_id_address_no UNIQUE (tenant_id, address_no), 
	CONSTRAINT uq_address_tenant_id_scope_node_id_address_key UNIQUE (tenant_id, scope_node_id, address_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_address_tenant_id_lga_key ON mdm.address (tenant_id, lga_key);

CREATE INDEX ix_address_tenant_id_paf_key ON mdm.address (tenant_id, paf_key);

CREATE INDEX ix_address_tenant_id_postcode ON mdm.address (tenant_id, postcode);

CREATE TABLE mdm.address_type (
	id BINARY(16) NOT NULL, 
	code VARCHAR(24) NOT NULL, 
	description VARCHAR(100) NOT NULL, 
	applies_to VARCHAR(16) NOT NULL, 
	active BOOL NOT NULL, 
	CONSTRAINT pk_address_type PRIMARY KEY (id), 
	CONSTRAINT ck_address_type_applies_to CHECK (applies_to IN ('person','organisation','any')), 
	CONSTRAINT uq_address_type_code UNIQUE (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE mdm.person (
	given_name VARCHAR(100) NOT NULL, 
	family_name VARCHAR(100) NOT NULL, 
	other_names VARCHAR(200), 
	preferred_name VARCHAR(100), 
	name_script VARCHAR(4) NOT NULL, 
	date_of_birth DATE, 
	sex_at_registration VARCHAR(1) NOT NULL, 
	nationality VARCHAR(2), 
	primary_language VARCHAR(3), 
	given_key VARCHAR(16), 
	family_key VARCHAR(16), 
	status VARCHAR(8) NOT NULL, 
	merged_into_id BINARY(16), 
	last_confirmed_at DATETIME(6), 
	quality_score INTEGER, 
	quality JSON, 
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
	CONSTRAINT pk_person PRIMARY KEY (id), 
	CONSTRAINT ck_person_status CHECK (status IN ('active','merged')), 
	CONSTRAINT ck_person_sex CHECK (sex_at_registration IN ('F','M','X','U')), 
	CONSTRAINT fk_person_merged_into_id_person FOREIGN KEY(merged_into_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_person_tenant_id_date_of_birth ON mdm.person (tenant_id, date_of_birth);

CREATE INDEX ix_person_tenant_id_family_key_given_key ON mdm.person (tenant_id, family_key, given_key);

CREATE INDEX ix_person_tenant_id_status ON mdm.person (tenant_id, status);

CREATE TABLE mdm.external_identifier (
	person_id BINARY(16) NOT NULL, 
	scheme VARCHAR(16) NOT NULL, 
	value_enc VARBINARY(255) NOT NULL, 
	value_hmac BINARY(32) NOT NULL, 
	display_suffix VARCHAR(4) NOT NULL, 
	verified_at DATETIME(6), 
	verified_by_adapter VARCHAR(32), 
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
	CONSTRAINT pk_external_identifier PRIMARY KEY (id), 
	CONSTRAINT uq_external_identifier_tenant_id_scheme_value_hmac UNIQUE (tenant_id, scheme, value_hmac), 
	CONSTRAINT fk_external_identifier_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_external_identifier_tenant_id_person_id ON mdm.external_identifier (tenant_id, person_id);

CREATE TABLE mdm.match_review (
	person_id BINARY(16) NOT NULL, 
	candidate_id BINARY(16) NOT NULL, 
	score INTEGER NOT NULL, 
	reasons JSON NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	decided_by BINARY(16), 
	decided_at DATETIME(6), 
	decision_note VARCHAR(500), 
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
	CONSTRAINT pk_match_review PRIMARY KEY (id), 
	CONSTRAINT ck_match_review_status CHECK (status IN ('open','same_person','different_people')), 
	CONSTRAINT fk_match_review_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id), 
	CONSTRAINT fk_match_review_candidate_id_person FOREIGN KEY(candidate_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_match_review_tenant_id_person_id ON mdm.match_review (tenant_id, person_id);

CREATE INDEX ix_match_review_tenant_id_status ON mdm.match_review (tenant_id, status);

CREATE TABLE mdm.person_contact (
	person_id BINARY(16) NOT NULL, 
	kind VARCHAR(8) NOT NULL, 
	value VARCHAR(254) NOT NULL, 
	is_primary BOOL NOT NULL, 
	verified_at DATETIME(6), 
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
	CONSTRAINT pk_person_contact PRIMARY KEY (id), 
	CONSTRAINT ck_person_contact_kind CHECK (kind IN ('phone','email')), 
	CONSTRAINT fk_person_contact_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_person_contact_tenant_id_person_id ON mdm.person_contact (tenant_id, person_id);

CREATE INDEX ix_person_contact_tenant_id_value ON mdm.person_contact (tenant_id, value);

CREATE TABLE mdm.person_name (
	person_id BINARY(16) NOT NULL, 
	name_type VARCHAR(16) NOT NULL, 
	given_name VARCHAR(100) NOT NULL, 
	family_name VARCHAR(100) NOT NULL, 
	other_names VARCHAR(200), 
	name_script VARCHAR(4) NOT NULL, 
	family_key VARCHAR(16), 
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
	CONSTRAINT pk_person_name PRIMARY KEY (id), 
	CONSTRAINT ck_person_name_name_type CHECK (name_type IN ('previous','alias','transliteration')), 
	CONSTRAINT fk_person_name_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_person_name_tenant_id_family_key ON mdm.person_name (tenant_id, family_key);

CREATE INDEX ix_person_name_tenant_id_person_id ON mdm.person_name (tenant_id, person_id);

CREATE TABLE mdm.party_address (
	address_id BINARY(16) NOT NULL, 
	person_id BINARY(16), 
	institution_id BINARY(16), 
	address_type_id BINARY(16) NOT NULL, 
	effective_from DATE NOT NULL, 
	effective_to DATE, 
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
	CONSTRAINT pk_party_address PRIMARY KEY (id), 
	CONSTRAINT ck_party_address_one_party CHECK ((person_id IS NULL AND institution_id IS NOT NULL) OR (person_id IS NOT NULL AND institution_id IS NULL)), 
	CONSTRAINT fk_party_address_address_id_address FOREIGN KEY(address_id) REFERENCES mdm.address (id), 
	CONSTRAINT fk_party_address_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id), 
	CONSTRAINT fk_party_address_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id), 
	CONSTRAINT fk_party_address_address_type_id_address_type FOREIGN KEY(address_type_id) REFERENCES mdm.address_type (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_party_address_tenant_id_address_id ON mdm.party_address (tenant_id, address_id);

CREATE INDEX ix_party_address_tenant_id_institution_id ON mdm.party_address (tenant_id, institution_id);

CREATE INDEX ix_party_address_tenant_id_person_id ON mdm.party_address (tenant_id, person_id);

CREATE TABLE mdm.person_merge (
	survivor_id BINARY(16) NOT NULL, 
	merged_id BINARY(16) NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	snapshot JSON NOT NULL, 
	decisions JSON NOT NULL, 
	reason VARCHAR(500) NOT NULL, 
	review_id BINARY(16), 
	reversed_by BINARY(16), 
	reversed_at DATETIME(6), 
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
	CONSTRAINT pk_person_merge PRIMARY KEY (id), 
	CONSTRAINT ck_person_merge_status CHECK (status IN ('applied','reversed')), 
	CONSTRAINT fk_person_merge_survivor_id_person FOREIGN KEY(survivor_id) REFERENCES mdm.person (id), 
	CONSTRAINT fk_person_merge_merged_id_person FOREIGN KEY(merged_id) REFERENCES mdm.person (id), 
	CONSTRAINT fk_person_merge_review_id_match_review FOREIGN KEY(review_id) REFERENCES mdm.match_review (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_person_merge_tenant_id_merged_id ON mdm.person_merge (tenant_id, merged_id);

CREATE INDEX ix_person_merge_tenant_id_survivor_id ON mdm.person_merge (tenant_id, survivor_id);
