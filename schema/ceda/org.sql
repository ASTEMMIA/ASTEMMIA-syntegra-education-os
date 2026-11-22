-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS org;

CREATE TABLE org.education_body (
	id BINARY(16) NOT NULL, 
	code VARCHAR(24) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	scope VARCHAR(16) NOT NULL, 
	countries JSON NOT NULL, 
	roles JSON NOT NULL, 
	covers JSON NOT NULL, 
	examinations JSON NOT NULL, 
	note VARCHAR(500), 
	catalogue VARCHAR(32) NOT NULL, 
	active BOOL NOT NULL, 
	CONSTRAINT pk_education_body PRIMARY KEY (id), 
	CONSTRAINT ck_education_body_scope CHECK (scope IN ('international','regional','national','state')), 
	CONSTRAINT uq_education_body_code UNIQUE (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE org.institution (
	institution_code VARCHAR(20) NOT NULL, 
	government_code VARCHAR(40), 
	name VARCHAR(200) NOT NULL, 
	name_key VARCHAR(200) NOT NULL, 
	parent_scope_id BINARY(16) NOT NULL, 
	location VARCHAR(64) NOT NULL, 
	location_key VARCHAR(64) NOT NULL, 
	institution_type VARCHAR(24) NOT NULL, 
	ownership VARCHAR(8) NOT NULL, 
	managing_body VARCHAR(64), 
	status VARCHAR(8) NOT NULL, 
	town VARCHAR(100), 
	address_line1 VARCHAR(255), 
	address_line2 VARCHAR(255), 
	postcode VARCHAR(16), 
	phone VARCHAR(20), 
	email VARCHAR(254), 
	latitude NUMERIC(9, 6), 
	longitude NUMERIC(9, 6), 
	head_person_id BINARY(16), 
	own_tenant_id BINARY(16), 
	proprietor_name VARCHAR(200), 
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
	CONSTRAINT uq_institution_tenant_id_parent_scope_id_location_key_name_key UNIQUE (tenant_id, parent_scope_id, location_key, name_key), 
	CONSTRAINT ck_institution_ownership CHECK (ownership IN ('public','private')), 
	CONSTRAINT ck_institution_institution_type CHECK (institution_type IN ('school','university','polytechnic','college_of_education','technical_college','college_of_health','other_tertiary','adult_learning_centre')), 
	CONSTRAINT ck_institution_status CHECK (status IN ('active','closed')), 
	CONSTRAINT fk_institution_head_person_id_person FOREIGN KEY(head_person_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_institution_tenant_id_location_key ON org.institution (tenant_id, location_key);

CREATE INDEX ix_institution_tenant_id_parent_scope_id ON org.institution (tenant_id, parent_scope_id);

CREATE INDEX ix_institution_tenant_id_postcode ON org.institution (tenant_id, postcode);

CREATE TABLE org.institution_body_link (
	institution_id BINARY(16) NOT NULL, 
	body_id BINARY(16) NOT NULL, 
	link_type VARCHAR(20) NOT NULL, 
	exam_code VARCHAR(24), 
	reference VARCHAR(40), 
	status VARCHAR(12) NOT NULL, 
	valid_from DATE, 
	valid_to DATE, 
	note VARCHAR(255), 
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
	CONSTRAINT pk_institution_body_link PRIMARY KEY (id), 
	CONSTRAINT uq_institution_body_link_body_id_link_type_exam_code_reference UNIQUE (body_id, link_type, exam_code, reference), 
	CONSTRAINT ck_institution_body_link_link_type CHECK (link_type IN ('exam_centre','admissions_code','accreditation','approval','beneficiary')), 
	CONSTRAINT ck_institution_body_link_status CHECK (status IN ('active','provisional','suspended','withdrawn')), 
	CONSTRAINT fk_institution_body_link_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id), 
	CONSTRAINT fk_institution_body_link_body_id_education_body FOREIGN KEY(body_id) REFERENCES org.education_body (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_institution_body_link_tenant_id_institution_id ON org.institution_body_link (tenant_id, institution_id);

CREATE TABLE org.institution_level (
	institution_id BINARY(16) NOT NULL, 
	level VARCHAR(16) NOT NULL, 
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
	CONSTRAINT ck_institution_level_level CHECK (level IN ('eccde','primary','jss','sss','tvet','nce','nd','hnd','degree','postgraduate','adult')), 
	CONSTRAINT fk_institution_level_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE org.registration_application (
	reference VARCHAR(20) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	regulator_code VARCHAR(64) NOT NULL, 
	details JSON NOT NULL, 
	name_key VARCHAR(200) NOT NULL, 
	location_key VARCHAR(64) NOT NULL, 
	proprietor_name VARCHAR(200) NOT NULL, 
	proprietor_email VARCHAR(254) NOT NULL, 
	proprietor_phone VARCHAR(20) NOT NULL, 
	cac_number VARCHAR(20), 
	history JSON NOT NULL, 
	email_verified_at DATETIME(6), 
	email_token_hash BINARY(32), 
	email_token_expires_at DATETIME(6), 
	verification_sends INTEGER NOT NULL, 
	last_verification_sent_at DATETIME(6), 
	decision_note VARCHAR(1000), 
	institution_id BINARY(16), 
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
	CONSTRAINT pk_registration_application PRIMARY KEY (id), 
	CONSTRAINT uq_registration_application_tenant_id_reference UNIQUE (tenant_id, reference), 
	CONSTRAINT ck_registration_application_status CHECK (status IN ('submitted','under_review','site_inspection','approved','rejected')), 
	CONSTRAINT fk_registration_application_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_registration_application_tenant_id_location_key_name_key ON org.registration_application (tenant_id, location_key, name_key);

CREATE INDEX ix_registration_application_tenant_id_status ON org.registration_application (tenant_id, status);
