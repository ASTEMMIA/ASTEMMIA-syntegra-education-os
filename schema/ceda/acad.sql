-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS acad;

CREATE TABLE acad.appraisal_cycle (
	name VARCHAR(100) NOT NULL, 
	period_start DATE NOT NULL, 
	period_end DATE NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	criteria JSON NOT NULL, 
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
	CONSTRAINT pk_appraisal_cycle PRIMARY KEY (id), 
	CONSTRAINT uq_appraisal_cycle_tenant_id_scope_node_id_name UNIQUE (tenant_id, scope_node_id, name), 
	CONSTRAINT ck_appraisal_cycle_status CHECK (status IN ('open','closed')), 
	CONSTRAINT ck_appraisal_cycle_period CHECK (period_end >= period_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE acad.staff (
	person_id BINARY(16) NOT NULL, 
	staff_number VARCHAR(20) NOT NULL, 
	employer_scope_id BINARY(16) NOT NULL, 
	category VARCHAR(16) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	government_file_number VARCHAR(40), 
	first_appointment_date DATE, 
	confirmation_date DATE, 
	exit_date DATE, 
	exit_reason VARCHAR(20), 
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
	CONSTRAINT pk_staff PRIMARY KEY (id), 
	CONSTRAINT uq_staff_tenant_id_person_id UNIQUE (tenant_id, person_id), 
	CONSTRAINT uq_staff_tenant_id_staff_number UNIQUE (tenant_id, staff_number), 
	CONSTRAINT uq_staff_tenant_id_government_file_number UNIQUE (tenant_id, government_file_number), 
	CONSTRAINT ck_staff_category CHECK (category IN ('teaching','non_teaching')), 
	CONSTRAINT ck_staff_status CHECK (status IN ('active','suspended','on_leave','exited')), 
	CONSTRAINT fk_staff_person_id_person FOREIGN KEY(person_id) REFERENCES mdm.person (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_staff_tenant_id_status ON acad.staff (tenant_id, status);

CREATE TABLE acad.appraisal (
	cycle_id BINARY(16) NOT NULL, 
	staff_id BINARY(16) NOT NULL, 
	status VARCHAR(14) NOT NULL, 
	appraiser_id BINARY(16), 
	countersigner_id BINARY(16), 
	ratings JSON NOT NULL, 
	score NUMERIC(5, 2), 
	band VARCHAR(16), 
	strengths VARCHAR(1000), 
	improvements VARCHAR(1000), 
	development_plan VARCHAR(1000), 
	recommendation VARCHAR(20) NOT NULL, 
	appraiser_comment VARCHAR(1000), 
	appraisee_comment VARCHAR(1000), 
	countersigner_comment VARCHAR(1000), 
	submitted_at DATETIME(6), 
	countersigned_at DATETIME(6), 
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
	CONSTRAINT pk_appraisal PRIMARY KEY (id), 
	CONSTRAINT uq_appraisal_tenant_id_cycle_id_staff_id UNIQUE (tenant_id, cycle_id, staff_id), 
	CONSTRAINT ck_appraisal_status CHECK (status IN ('draft','submitted','returned','countersigned')), 
	CONSTRAINT ck_appraisal_recommendation CHECK (recommendation IN ('none','promotion','commendation','training','improvement_plan')), 
	CONSTRAINT fk_appraisal_cycle_id_appraisal_cycle FOREIGN KEY(cycle_id) REFERENCES acad.appraisal_cycle (id), 
	CONSTRAINT fk_appraisal_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_appraisal_tenant_id_staff_id ON acad.appraisal (tenant_id, staff_id);

CREATE TABLE acad.lesson_observation (
	staff_id BINARY(16) NOT NULL, 
	observed_by BINARY(16), 
	observed_on DATE NOT NULL, 
	class_name VARCHAR(60) NOT NULL, 
	subject VARCHAR(80) NOT NULL, 
	topic VARCHAR(200), 
	ratings JSON NOT NULL, 
	score NUMERIC(5, 2) NOT NULL, 
	strengths VARCHAR(1000), 
	improvements VARCHAR(1000), 
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
	CONSTRAINT pk_lesson_observation PRIMARY KEY (id), 
	CONSTRAINT fk_lesson_observation_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_lesson_observation_tenant_id_staff_id ON acad.lesson_observation (tenant_id, staff_id);

CREATE TABLE acad.posting_request (
	staff_id BINARY(16) NOT NULL, 
	to_scope_id BINARY(16) NOT NULL, 
	to_institution_id BINARY(16), 
	position VARCHAR(100) NOT NULL, 
	grade_level INTEGER, 
	step INTEGER, 
	salary_structure VARCHAR(16), 
	appointment_type VARCHAR(20) NOT NULL, 
	effective_date DATE NOT NULL, 
	reason VARCHAR(255), 
	reference VARCHAR(60), 
	status VARCHAR(12) NOT NULL, 
	requested_by BINARY(16), 
	decided_by BINARY(16), 
	decided_at DATETIME(6), 
	decision_note VARCHAR(255), 
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
	CONSTRAINT pk_posting_request PRIMARY KEY (id), 
	CONSTRAINT ck_posting_request_status CHECK (status IN ('pending','approved','rejected','cancelled')), 
	CONSTRAINT fk_posting_request_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id), 
	CONSTRAINT fk_posting_request_to_institution_id_institution FOREIGN KEY(to_institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_posting_request_tenant_id_staff_id ON acad.posting_request (tenant_id, staff_id);

CREATE INDEX ix_posting_request_tenant_id_status ON acad.posting_request (tenant_id, status);

CREATE TABLE acad.staff_post (
	staff_id BINARY(16) NOT NULL, 
	post_scope_id BINARY(16) NOT NULL, 
	institution_id BINARY(16), 
	position VARCHAR(100) NOT NULL, 
	grade_level INTEGER, 
	step INTEGER, 
	salary_structure VARCHAR(16), 
	is_primary BOOL NOT NULL, 
	fte NUMERIC(3, 2) NOT NULL, 
	appointment_type VARCHAR(20) NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE, 
	reference VARCHAR(60), 
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
	CONSTRAINT pk_staff_post PRIMARY KEY (id), 
	CONSTRAINT ck_staff_post_grade CHECK (grade_level IS NULL OR grade_level BETWEEN 1 AND 17), 
	CONSTRAINT ck_staff_post_step CHECK (step IS NULL OR step BETWEEN 1 AND 15), 
	CONSTRAINT ck_staff_post_fte CHECK (fte > 0 AND fte <= 1), 
	CONSTRAINT ck_staff_post_appointment_type CHECK (appointment_type IN ('first_appointment','posting','transfer','promotion','acting','secondment','redeployment','contract')), 
	CONSTRAINT ck_staff_post_dates CHECK (end_date IS NULL OR end_date >= start_date), 
	CONSTRAINT fk_staff_post_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id), 
	CONSTRAINT fk_staff_post_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_staff_post_tenant_id_institution_id ON acad.staff_post (tenant_id, institution_id);

CREATE INDEX ix_staff_post_tenant_id_staff_id ON acad.staff_post (tenant_id, staff_id);

CREATE TABLE acad.staff_qualification (
	staff_id BINARY(16) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	title VARCHAR(150) NOT NULL, 
	awarding_body VARCHAR(200), 
	year INTEGER, 
	teaching BOOL NOT NULL, 
	verified_at DATETIME(6), 
	verified_by BINARY(16), 
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
	CONSTRAINT pk_staff_qualification PRIMARY KEY (id), 
	CONSTRAINT ck_staff_qualification_kind CHECK (kind IN ('phd','masters','pgde','degree','hnd','nce','nd','diploma','certificate','other')), 
	CONSTRAINT fk_staff_qualification_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_staff_qualification_tenant_id_staff_id ON acad.staff_qualification (tenant_id, staff_id);

CREATE TABLE acad.teacher_registration (
	staff_id BINARY(16) NOT NULL, 
	body_code VARCHAR(24) NOT NULL, 
	number VARCHAR(40) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	registered_on DATE, 
	expires_on DATE, 
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
	CONSTRAINT pk_teacher_registration PRIMARY KEY (id), 
	CONSTRAINT uq_teacher_registration_tenant_id_body_code_number UNIQUE (tenant_id, body_code, number), 
	CONSTRAINT ck_teacher_registration_status CHECK (status IN ('registered','provisional','expired','suspended')), 
	CONSTRAINT fk_teacher_registration_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_teacher_registration_tenant_id_staff_id ON acad.teacher_registration (tenant_id, staff_id);
