-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS rec;

CREATE TABLE rec.digitisation_batch (
	institution_id BINARY(16) NOT NULL, 
	batch_number VARCHAR(20) NOT NULL, 
	operator_kind VARCHAR(10) NOT NULL, 
	contractor_name VARCHAR(200), 
	contract_reference VARCHAR(60), 
	operator_user_id BINARY(16) NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	captured_at DATETIME(6), 
	qc_by BINARY(16), 
	qc_at DATETIME(6), 
	qc_sampled INTEGER, 
	qc_defects INTEGER, 
	qc_note VARCHAR(500), 
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
	CONSTRAINT pk_digitisation_batch PRIMARY KEY (id), 
	CONSTRAINT uq_digitisation_batch_tenant_id_batch_number UNIQUE (tenant_id, batch_number), 
	CONSTRAINT ck_digitisation_batch_operator_kind CHECK (operator_kind IN ('in_house','contractor')), 
	CONSTRAINT ck_digitisation_batch_status CHECK (status IN ('open','captured','accepted','rejected')), 
	CONSTRAINT ck_digitisation_batch_contractor CHECK (operator_kind = 'in_house' OR contractor_name IS NOT NULL), 
	CONSTRAINT fk_digitisation_batch_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_digitisation_batch_tenant_id_operator_user_id ON rec.digitisation_batch (tenant_id, operator_user_id);

CREATE TABLE rec.records_series (
	institution_id BINARY(16) NOT NULL, 
	plan VARCHAR(16) NOT NULL, 
	function_code VARCHAR(8) NOT NULL, 
	function_name VARCHAR(100) NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	activity VARCHAR(200), 
	numbering VARCHAR(12) NOT NULL, 
	classification VARCHAR(12) NOT NULL, 
	paper_retain_years INTEGER, 
	paper_rule VARCHAR(20) NOT NULL, 
	vital BOOL NOT NULL, 
	active BOOL NOT NULL, 
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
	CONSTRAINT pk_records_series PRIMARY KEY (id), 
	CONSTRAINT uq_records_series_tenant_id_institution_id_code UNIQUE (tenant_id, institution_id, code), 
	CONSTRAINT ck_records_series_numbering CHECK (numbering IN ('single','annual','self','alphabetical')), 
	CONSTRAINT ck_records_series_classification CHECK (classification IN ('public','internal','confidential','restricted','special')), 
	CONSTRAINT ck_records_series_paper_rule CHECK (paper_rule IN ('destroy','transfer_archives','retain')), 
	CONSTRAINT ck_records_series_years CHECK (paper_retain_years IS NULL OR paper_retain_years BETWEEN 0 AND 100), 
	CONSTRAINT fk_records_series_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec.paper_file (
	institution_id BINARY(16) NOT NULL, 
	series_id BINARY(16) NOT NULL, 
	file_number VARCHAR(40) NOT NULL, 
	seq INTEGER NOT NULL, 
	year INTEGER, 
	letter VARCHAR(1), 
	title VARCHAR(200) NOT NULL, 
	person_id BINARY(16), 
	classification VARCHAR(12) NOT NULL, 
	volume INTEGER NOT NULL, 
	previous_file_id BINARY(16), 
	opened_on DATE NOT NULL, 
	closed_on DATE, 
	status VARCHAR(12) NOT NULL, 
	home_location VARCHAR(120) NOT NULL, 
	holder_user_id BINARY(16), 
	holder_name VARCHAR(120), 
	out_since DATETIME(6), 
	due_back DATE, 
	digitised BOOL NOT NULL, 
	legal_hold BOOL NOT NULL, 
	keywords JSON NOT NULL, 
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
	CONSTRAINT pk_paper_file PRIMARY KEY (id), 
	CONSTRAINT uq_paper_file_tenant_id_institution_id_file_number_volume UNIQUE (tenant_id, institution_id, file_number, volume), 
	CONSTRAINT ck_paper_file_status CHECK (status IN ('open','closed','disposed','transferred')), 
	CONSTRAINT ck_paper_file_classification CHECK (classification IN ('public','internal','confidential','restricted','special')), 
	CONSTRAINT ck_paper_file_volume CHECK (volume >= 1), 
	CONSTRAINT ck_paper_file_dates CHECK (closed_on IS NULL OR closed_on >= opened_on), 
	CONSTRAINT fk_paper_file_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id), 
	CONSTRAINT fk_paper_file_series_id_records_series FOREIGN KEY(series_id) REFERENCES rec.records_series (id), 
	CONSTRAINT fk_paper_file_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id), 
	CONSTRAINT fk_paper_file_previous_file_id_paper_file FOREIGN KEY(previous_file_id) REFERENCES rec.paper_file (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_paper_file_tenant_id_person_id ON rec.paper_file (tenant_id, person_id);

CREATE INDEX ix_paper_file_tenant_id_series_id ON rec.paper_file (tenant_id, series_id);

CREATE INDEX ix_paper_file_tenant_id_status ON rec.paper_file (tenant_id, status);

CREATE TABLE rec.document (
	document_number VARCHAR(30) NOT NULL, 
	institution_id BINARY(16) NOT NULL, 
	series_id BINARY(16), 
	file_id BINARY(16), 
	folio INTEGER, 
	batch_id BINARY(16), 
	title VARCHAR(255) NOT NULL, 
	doc_type VARCHAR(20) NOT NULL, 
	classification VARCHAR(12) NOT NULL, 
	document_date DATE, 
	source VARCHAR(12) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	keywords JSON NOT NULL, 
	proposed JSON, 
	current_version INTEGER NOT NULL, 
	page_count INTEGER, 
	search_text MEDIUMTEXT NOT NULL, 
	captured_by BINARY(16), 
	indexed_by BINARY(16), 
	indexed_at DATETIME(6), 
	verified_by BINARY(16), 
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
	CONSTRAINT pk_document PRIMARY KEY (id), 
	CONSTRAINT uq_document_tenant_id_document_number UNIQUE (tenant_id, document_number), 
	CONSTRAINT ck_document_classification CHECK (classification IN ('public','internal','confidential','restricted','special')), 
	CONSTRAINT ck_document_status CHECK (status IN ('captured','indexed','verified')), 
	CONSTRAINT ck_document_source CHECK (source IN ('scanned','born_digital','email','upload')), 
	CONSTRAINT fk_document_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id), 
	CONSTRAINT fk_document_series_id_records_series FOREIGN KEY(series_id) REFERENCES rec.records_series (id), 
	CONSTRAINT fk_document_file_id_paper_file FOREIGN KEY(file_id) REFERENCES rec.paper_file (id), 
	CONSTRAINT fk_document_batch_id_digitisation_batch FOREIGN KEY(batch_id) REFERENCES rec.digitisation_batch (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_document_tenant_id_batch_id ON rec.document (tenant_id, batch_id);

CREATE INDEX ix_document_tenant_id_file_id ON rec.document (tenant_id, file_id);

CREATE INDEX ix_document_tenant_id_institution_id_status ON rec.document (tenant_id, institution_id, status);

CREATE TABLE rec.file_movement (
	file_id BINARY(16) NOT NULL, 
	kind VARCHAR(14) NOT NULL, 
	to_user_id BINARY(16), 
	to_name VARCHAR(120), 
	moved_at DATETIME(6) NOT NULL, 
	due_back DATE, 
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
	CONSTRAINT pk_file_movement PRIMARY KEY (id), 
	CONSTRAINT ck_file_movement_kind CHECK (kind IN ('issue','return','pass','external_out')), 
	CONSTRAINT fk_file_movement_file_id_paper_file FOREIGN KEY(file_id) REFERENCES rec.paper_file (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_file_movement_tenant_id_file_id ON rec.file_movement (tenant_id, file_id);

CREATE TABLE rec.paper_disposition (
	file_id BINARY(16) NOT NULL, 
	action VARCHAR(20) NOT NULL, 
	reason VARCHAR(255) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	requested_by BINARY(16), 
	decided_by BINARY(16), 
	decided_at DATETIME(6), 
	decision_note VARCHAR(255), 
	certificate_reference VARCHAR(60), 
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
	CONSTRAINT pk_paper_disposition PRIMARY KEY (id), 
	CONSTRAINT ck_paper_disposition_action CHECK (action IN ('destroy','transfer_archives','retain')), 
	CONSTRAINT ck_paper_disposition_status CHECK (status IN ('pending','approved','rejected')), 
	CONSTRAINT fk_paper_disposition_file_id_paper_file FOREIGN KEY(file_id) REFERENCES rec.paper_file (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_paper_disposition_tenant_id_file_id ON rec.paper_disposition (tenant_id, file_id);

CREATE INDEX ix_paper_disposition_tenant_id_status ON rec.paper_disposition (tenant_id, status);

CREATE TABLE rec.document_access (
	document_id BINARY(16) NOT NULL, 
	user_id BINARY(16), 
	action VARCHAR(10) NOT NULL, 
	version INTEGER, 
	purpose VARCHAR(255), 
	at DATETIME(6) NOT NULL, 
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
	CONSTRAINT pk_document_access PRIMARY KEY (id), 
	CONSTRAINT ck_document_access_action CHECK (action IN ('view','download','print','share')), 
	CONSTRAINT fk_document_access_document_id_document FOREIGN KEY(document_id) REFERENCES rec.document (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_document_access_tenant_id_document_id ON rec.document_access (tenant_id, document_id);

CREATE INDEX ix_document_access_tenant_id_user_id ON rec.document_access (tenant_id, user_id);

CREATE TABLE rec.document_link (
	document_id BINARY(16) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	target_id BINARY(16) NOT NULL, 
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
	CONSTRAINT pk_document_link PRIMARY KEY (id), 
	CONSTRAINT uq_document_link_tenant_id_document_id_kind_target_id UNIQUE (tenant_id, document_id, kind, target_id), 
	CONSTRAINT ck_document_link_kind CHECK (kind IN ('learner','staff','person','institution','application')), 
	CONSTRAINT fk_document_link_document_id_document FOREIGN KEY(document_id) REFERENCES rec.document (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_document_link_tenant_id_kind_target_id ON rec.document_link (tenant_id, kind, target_id);

CREATE TABLE rec.document_version (
	document_id BINARY(16) NOT NULL, 
	version INTEGER NOT NULL, 
	blob_key VARCHAR(200) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	mime VARCHAR(40) NOT NULL, 
	original_name VARCHAR(255), 
	ocr_text MEDIUMTEXT, 
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
	CONSTRAINT pk_document_version PRIMARY KEY (id), 
	CONSTRAINT uq_document_version_tenant_id_document_id_version UNIQUE (tenant_id, document_id, version), 
	CONSTRAINT uq_document_version_tenant_id_blob_key UNIQUE (tenant_id, blob_key), 
	CONSTRAINT fk_document_version_document_id_document FOREIGN KEY(document_id) REFERENCES rec.document (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
