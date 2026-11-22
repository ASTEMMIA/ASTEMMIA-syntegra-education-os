"""What the browser app needs from the API (ADR-028): names, user lookup, and /app itself."""

import httpx
import pytest

from syntegra.platform.settings import Settings
from syntegra.web.app import create_app
from tests.integration.test_records import _setup


async def _login(client, name, scope):
    r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
    return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Scope-Node-Id": str(scope)}


async def test_me_names_and_user_lookup(client, sm, world):
    a = await _setup(sm, world)
    recs = await _login(client, "records@central.example", a.school)
    tch = await _login(client, "teacher@central.example", a.school)
    me = (await client.get("/v1/me", headers=recs)).json()
    assert me["display_name"] == "records@central.example"
    assert me["tenant_code"] == "NG-AB"
    [g] = me["grants"]
    assert (g["role"], g["role_name"], g["scope_name"]) == (
        "REC",
        "Records officer",
        "TEST Central School Aba",
    )
    assert me["permissions_here"]["records:admin"] == "Y"
    r = await client.get("/v1/users/names", params={"ids": f"{a.head},{a.capt}"}, headers=tch)
    assert r.json() == {
        str(a.head): "head@central.example",
        str(a.capt): "scanner@contractor.example",
    }
    r = await client.get("/v1/users", params={"q": "contractor"}, headers=recs)
    assert {u["login"] for u in r.json()["items"]} == {
        "scanner@contractor.example",
        "other@contractor.example",
    }
    assert (await client.get("/v1/users", params={"q": "scan"}, headers=tch)).status_code == 403
    r = await client.get("/v1/users/names", params={"ids": "not-a-uuid"}, headers=tch)
    assert r.status_code == 422


@pytest.fixture
def built(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Records</title>")
    (tmp_path / "assets" / "main-abc123.js").write_text("console.log(1)")
    return tmp_path


async def test_app_is_served_with_fallback_and_headers(built):
    app = create_app(Settings(env="test", database_url="sqlite+aiosqlite://", web_dir=str(built)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/app")
        assert r.status_code == 308 and r.headers["location"] == "/app/"
        for path in ("/app/", "/app/documents/123", "/app/files/x/movements"):
            r = await c.get(path)
            assert r.status_code == 200 and "<title>Records</title>" in r.text
            assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
            assert r.headers["cache-control"] == "no-cache"
        r = await c.get("/app/assets/main-abc123.js")
        assert r.headers["cache-control"].endswith("immutable")
        assert (await c.get("/app/assets/missing.js")).status_code == 404
        r = await c.get("/app/../pyproject.toml")
        assert "pyproject" not in r.text
    bare = create_app(Settings(env="test", database_url="sqlite+aiosqlite://", web_dir="/nope"))
    assert "/app/{path:path}" not in {getattr(r, "path", "") for r in bare.routes}
