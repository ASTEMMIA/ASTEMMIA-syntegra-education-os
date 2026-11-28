import uuid

ACCOUNTS = {
    "padm": ("SYNTEGRA", "admin@syntegra.test"),
    "state_officer": ("NG-AB-MOE", "officer@abia.test"),
    "head_a": ("INS-NG-000101", "head@gcu.test"),
    "teacher_a": ("INS-NG-000101", "teacher@gcu.test"),
    "head_b": ("INS-NG-000102", "head@academy.test"),
}


async def as_(client, who, scope=None, **extra):
    """Sign in through the dev login and return headers for an authenticated request."""
    tenant_code, login = ACCOUNTS[who]
    r = await client.post("/v1/auth/dev-login", json={"tenant_code": tenant_code, "login": login})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}", **extra}
    if scope is not None:
        headers["X-Scope-Node-Id"] = str(scope)
    return headers


async def test_health(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200


async def test_me_shows_effective_permissions(client, world):
    r = await client.get("/v1/me", headers=await as_(client, "teacher_a", world.school_a_campus_1))
    assert r.status_code == 200
    body = r.json()
    assert body["permissions_here"]["learner:view"] == "C"
    assert "safeguarding_case:view" not in body["permissions_here"]


async def test_unauthenticated_and_foreign_scope_are_rejected(client, world):
    r = await client.get("/v1/me")
    assert r.status_code == 401 and r.headers["content-type"] == "application/problem+json"
    assert r.json()["next_action"]
    # Teacher cannot switch into the sibling campus where they hold no role.
    r = await client.get("/v1/me", headers=await as_(client, "teacher_a", world.school_a_campus_2))
    assert r.status_code == 403
    # Head of school A cannot claim school B's scope.
    r = await client.get("/v1/me", headers=await as_(client, "head_a", world.school_b_root))
    assert r.status_code == 403


async def test_create_scope_node_with_idempotency(client, world):
    h = await as_(client, "head_b", world.school_b_root)
    body = {
        "parent_id": str(world.school_b_root),
        "level": 8,
        "code": "SCIENCE",
        "name": "Science department",
    }
    r = await client.post("/v1/scope-nodes", json=body, headers=h)
    assert r.status_code == 428
    key = str(uuid.uuid4())
    r1 = await client.post("/v1/scope-nodes", json=body, headers=h | {"Idempotency-Key": key})
    assert r1.status_code == 201, r1.text
    r2 = await client.post("/v1/scope-nodes", json=body, headers=h | {"Idempotency-Key": key})
    assert (
        r2.status_code == 201
        and r2.json() == r1.json()
        and r2.headers["Idempotent-Replayed"] == "true"
    )
    r3 = await client.post(
        "/v1/scope-nodes", json=body | {"name": "Other"}, headers=h | {"Idempotency-Key": key}
    )
    assert r3.status_code == 422
    children = await client.get(
        f"/v1/scope-nodes/{world.school_b_root}/children?limit=1", headers=h
    )
    assert [c["code"] for c in children.json()["items"]] == ["SCIENCE"]


async def test_teacher_cannot_manage_scopes(client, world):
    h = await as_(client, "teacher_a", world.school_a_campus_1, **{"Idempotency-Key": "k1"})
    body = {
        "parent_id": str(world.school_a_campus_1),
        "level": 9,
        "code": "JSS1A",
        "name": "JSS 1A",
    }
    r = await client.post("/v1/scope-nodes", json=body, headers=h)
    assert r.status_code == 403
    assert r.json()["type"].endswith("/forbidden")


async def test_cannot_read_scope_outside_subtree(client, world):
    h = await as_(client, "head_b", world.school_b_root)
    r = await client.get(f"/v1/scope-nodes/{world.school_a_root}", headers=h)
    assert r.status_code == 404  # finding a record never grants reading it


async def test_state_officer_creates_institution_tenant(client, world):
    h = await as_(client, "state_officer", world.abia_root, **{"Idempotency-Key": "t1"})
    body = {
        "parent_scope_id": str(world.lgea),
        "code": "INS-NG-000103",
        "name": "Community Secondary School",
        "level": 6,
        "ownership": "public",
    }
    r = await client.post("/v1/tenants", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "institutional"
    bad = body | {"code": "LGEA-2", "level": 4}
    r = await client.post("/v1/tenants", json=bad, headers=h | {"Idempotency-Key": "t2"})
    assert r.status_code == 422 and "cannot be a tenant" in r.json()["detail"]


async def test_policy_rule_lifecycle_and_audit(client, world):
    h = await as_(client, "state_officer", world.abia_root)
    rule = {
        "owner_scope_id": str(world.abia_root),
        "rule_key": "ATT-INST-02",
        "domain": "attendance",
        "title": "Persistent absence",
        "expression": "learner.attendance_rate_6w < min_rate",
        "parameters": {"min_rate": 0.9},
        "strictness": {"min_rate": "min"},
        "valid_from": "2026-09-01",
        "test_cases": [
            {"name": "low", "facts": {"learner.attendance_rate_6w": 0.5}, "expect": True}
        ],
    }
    dry = await client.post(
        "/v1/policy-rules/test",
        json={k: rule[k] for k in ("expression", "parameters", "test_cases")},
        headers=h,
    )
    assert dry.json() == {"passed": True, "failures": []}
    r = await client.post("/v1/policy-rules", json=rule, headers=h | {"Idempotency-Key": "p1"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = await client.post(f"/v1/policy-rules/{rid}/publish", headers=h | {"Idempotency-Key": "p2"})
    assert r.status_code == 204, r.text
    unsafe = rule | {"expression": "__import__('os')", "rule_key": "ATT-INST-03"}
    r = await client.post("/v1/policy-rules", json=unsafe, headers=h | {"Idempotency-Key": "p3"})
    assert r.status_code == 422


async def test_audit_verify_requires_permission(client, world):
    r = await client.get(
        "/v1/audit/verify",
        headers=await as_(client, "teacher_a", world.school_a_campus_1),
    )
    assert r.status_code == 403
    r = await client.get(
        "/v1/audit/verify", headers=await as_(client, "head_a", world.school_a_root)
    )
    assert r.status_code == 200 and r.json()["intact"] is True and r.json()["events"] > 0
