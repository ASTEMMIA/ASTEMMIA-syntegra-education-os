from tests.integration.test_api import as_

ADA = {
    "given_name": "Adébáyọ̀",
    "family_name": "Ọláòyè",
    "date_of_birth": "2014-03-07",
    "sex_at_registration": "M",
    "nationality": "NG",
    "primary_language": "yo",
    "contacts": [{"kind": "phone", "value": "0803 123 4567", "is_primary": True}],
    "identifiers": [{"scheme": "NIN", "value": "12345678901"}],
}


async def _create(client, who="registrar_a", body=ADA, key="p1"):
    return await client.post(
        "/v1/persons", json=body, headers=await as_(client, who, **{"Idempotency-Key": key})
    )


async def test_registrar_registers_and_reads_masked_record(client, world):
    r = await _create(client)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["identifiers"][0]["masked"] == "•••••••8901" and "12345678901" not in r.text
    assert p["quality"]["score"] >= 90
    g = await client.get(f"/v1/persons/{p['id']}", headers=await as_(client, "head_a"))
    assert g.status_code == 200 and g.headers["etag"] == f'"{p["row_version"]}"'


async def test_roles_are_enforced(client, world):
    assert (await _create(client, who="teacher_a")).status_code == 403
    assert (
        await _create(client, who="head_a")
    ).status_code == 403  # heads view, registrars register
    p = (await _create(client)).json()
    h = await as_(client, "head_b")
    assert (
        await client.get(f"/v1/persons/{p['id']}", headers=h)
    ).status_code == 404  # other school


async def test_duplicate_is_blocked_with_explained_candidates(client, world):
    await _create(client)
    r = await _create(client, body=ADA | {"given_name": "Bayo", "contacts": []}, key="p2")
    assert r.status_code == 409 and r.json()["type"].endswith("/possible-duplicate")
    preview = await client.post(
        "/v1/persons/match",
        json=ADA | {"identifiers": []},
        headers=await as_(client, "registrar_a"),
    )
    c = preview.json()["candidates"][0]
    assert (
        c["visible"]
        and c["outcome"] == "review"
        and {x["code"] for x in c["reasons"]} >= {"dob_exact"}
    )


async def test_patch_needs_if_match(client, world):
    p = (await _create(client)).json()
    h = await as_(client, "registrar_a")
    url = f"/v1/persons/{p['id']}"
    assert (await client.patch(url, json={"family_name": "Okon"}, headers=h)).status_code == 428
    assert (
        await client.patch(url, json={"family_name": "Okon"}, headers=h | {"If-Match": '"99"'})
    ).status_code == 412
    ok = await client.patch(
        url, json={"family_name": "Okon"}, headers=h | {"If-Match": f'"{p["row_version"]}"'}
    )
    assert ok.status_code == 200 and ok.json()["other_recorded_names"][0]["type"] == "previous"


async def test_reveal_needs_permission_and_purpose(client, world):
    p = (await _create(client)).json()
    url = f"/v1/persons/{p['id']}/identifiers/{p['identifiers'][0]['id']}/reveal"
    assert (await client.post(url, headers=await as_(client, "teacher_a"))).status_code in (
        403,
        404,
    )
    assert (await client.post(url, headers=await as_(client, "registrar_a"))).status_code == 422
    r = await client.post(
        url, headers=await as_(client, "registrar_a", **{"X-Purpose": "waec-registration"})
    )
    assert (
        r.status_code == 200
        and r.json() == {"value": "12345678901"}
        and r.headers["cache-control"] == "no-store"
    )


async def test_review_decision_merges_through_the_api(client, world):
    a = (await _create(client, body=ADA | {"identifiers": []})).json()
    b = (
        await _create(
            client,
            body=ADA | {"given_name": "Adebayo", "family_name": "Olaoye", "contacts": []},
            key="p2",
        )
    ).json()
    assert b["possible_duplicates"] and b["review_ids"]
    h = await as_(client, "registrar_a")
    reviews = (await client.get("/v1/match-reviews", headers=h)).json()["items"]
    assert [r["id"] for r in reviews] == b["review_ids"]
    d = await client.post(
        f"/v1/match-reviews/{reviews[0]['id']}/decision",
        json={"decision": "same_person", "note": "Same birth certificate"},
        headers=h | {"Idempotency-Key": "d1"},
    )
    assert d.status_code == 200 and d.json()["merge_id"]
    alias = (await client.get(f"/v1/persons/{b['id']}", headers=h)).json()
    assert alias["id"] == a["id"] and alias["requested_id"] == b["id"]
    rev = await client.post(
        f"/v1/person-merges/{d.json()['merge_id']}/reverse",
        json={"reason": "Mistake"},
        headers=h | {"Idempotency-Key": "r1"},
    )
    assert rev.status_code == 200 and rev.json()["reversed"] is True
    assert (await client.get(f"/v1/persons/{b['id']}", headers=h)).json()["id"] == b["id"]
