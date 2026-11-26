"""Equipment "Upload spec sheet" flow: users extend the catalogue with their own products.

The feature is honest by construction: the human types every figure and an optional datasheet is
stored only as provenance (never parsed for numbers). These tests exercise the route pair
(``GET/POST /equipment/upload`` + ``GET /equipment/datasheet/{name}``) and the merge into
``equipment.catalogue()``, over an isolated ``$GRIDQ_RUN_STORE`` so nothing touches the real store.
"""

from __future__ import annotations

import io
import os
import tempfile

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-equip-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402

from gridq.app import create_app, equipment  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())


def test_upload_form_renders() -> None:
    resp = client.get("/equipment/upload")
    assert resp.status_code == 200
    body = resp.text
    assert 'action="/equipment/upload"' in body
    # Every category selectable, and its fields present for the no-JS path.
    for label in ("Solar panel", "Battery system", "Inverter"):
        assert label in body
    assert 'name="solar__power_w"' in body
    assert 'name="batteries__energy_kwh"' in body


def test_valid_battery_added_and_shown() -> None:
    before = len(equipment.catalogue()["batteries"])
    resp = client.post(
        "/equipment/upload",
        data={
            "category": "batteries",
            "maker": "Acme Cells",
            "model": "Vault 20",
            "batteries__energy_kwh": "19.5",
            "batteries__price": "230",
            "batteries__chemistry": "LFP",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text[:300]
    assert resp.headers["location"] == "/equipment?added=1"

    cat = equipment.catalogue()
    assert len(cat["batteries"]) == before + 1
    added = [b for b in cat["batteries"] if b["maker"] == "Acme Cells"]
    assert added and added[0]["model"] == "Vault 20"
    assert added[0]["source"] == "user"
    assert added[0]["energy_kwh"] == 19.5

    page = client.get("/equipment").text
    assert "Acme Cells" in page and "Vault 20" in page
    assert "Yours" in page  # the user-row marker


def test_solar_and_inverter_categories() -> None:
    r1 = client.post(
        "/equipment/upload",
        data={
            "category": "solar",
            "maker": "SunCo",
            "model": "Ray 500",
            "solar__power_w": "500",
            "solar__eff_pct": "21.5",
            "solar__price": "0.19",
        },
        follow_redirects=False,
    )
    assert r1.status_code == 303
    r2 = client.post(
        "/equipment/upload",
        data={
            "category": "inverters",
            "maker": "InvCo",
            "model": "Flow 12",
            "inverters__power_kw": "12",
            "inverters__price": "0.13",
            "inverters__hybrid": "1",
        },
        follow_redirects=False,
    )
    assert r2.status_code == 303
    cat = equipment.catalogue()
    assert any(p["maker"] == "SunCo" and p["source"] == "user" for p in cat["solar"])
    inv = [i for i in cat["inverters"] if i["maker"] == "InvCo"]
    assert inv and inv[0]["hybrid"] is True


@pytest.mark.parametrize(
    "data,needle",
    [
        (
            {
                "category": "solar",
                "maker": "",
                "model": "X",
                "solar__power_w": "400",
                "solar__eff_pct": "20",
                "solar__price": "0.2",
            },
            "Manufacturer and model",
        ),
        (
            {
                "category": "solar",
                "maker": "A",
                "model": "B",
                "solar__power_w": "abc",
                "solar__eff_pct": "20",
                "solar__price": "0.2",
            },
            "Power (W) is required",
        ),
        (
            {
                "category": "solar",
                "maker": "A",
                "model": "B",
                "solar__power_w": "400",
                "solar__eff_pct": "150",
                "solar__price": "0.2",
            },
            "Efficiency",
        ),
        ({"category": "nonsense", "maker": "A", "model": "B"}, "category"),
        (
            {
                "category": "batteries",
                "maker": "A",
                "model": "B",
                "batteries__energy_kwh": "10",
                "batteries__price": "200",
                "batteries__chemistry": "",
            },
            "Chemistry is required",
        ),
    ],
)
def test_invalid_submissions_re_render_without_500(data: dict[str, str], needle: str) -> None:
    before = equipment.catalogue()["counts"]
    resp = client.post("/equipment/upload", data=data, follow_redirects=False)
    assert resp.status_code == 200  # re-rendered form, never a redirect or a 500
    assert "Could not add that product" in resp.text
    assert needle in resp.text
    # Nothing was written for a rejected submission.
    assert equipment.catalogue()["counts"] == before


def test_datasheet_stored_served_and_linked() -> None:
    pdf = b"%PDF-1.4 fake datasheet bytes"
    resp = client.post(
        "/equipment/upload",
        data={
            "category": "solar",
            "maker": "DocCo",
            "model": "Sheet 1",
            "solar__power_w": "450",
            "solar__eff_pct": "22",
            "solar__price": "0.2",
        },
        files={"datasheet": ("panel.pdf", io.BytesIO(pdf), "application/pdf")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    row = next(p for p in equipment.catalogue()["solar"] if p["maker"] == "DocCo")
    name = row["datasheet"]
    assert name.endswith(".pdf")

    served = client.get(f"/equipment/datasheet/{name}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content == pdf

    # The Equipment page links the datasheet for that row.
    assert f"/equipment/datasheet/{name}" in client.get("/equipment").text


def test_bad_datasheet_type_rejected() -> None:
    resp = client.post(
        "/equipment/upload",
        data={
            "category": "solar",
            "maker": "ExeCo",
            "model": "Bad 1",
            "solar__power_w": "450",
            "solar__eff_pct": "22",
            "solar__price": "0.2",
        },
        files={"datasheet": ("panel.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "PDF, PNG or JPG" in resp.text
    assert not any(p["maker"] == "ExeCo" for p in equipment.catalogue()["solar"])


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "..%2f..%2fx.pdf", "notadigest.pdf", "abc.txt"]
)
def test_datasheet_route_rejects_unsafe_names(name: str) -> None:
    assert client.get(f"/equipment/datasheet/{name}").status_code == 404


def test_re_adding_same_product_overwrites_not_duplicates() -> None:
    payload = {
        "category": "solar",
        "maker": "DupCo",
        "model": "One",
        "solar__power_w": "400",
        "solar__eff_pct": "20",
        "solar__price": "0.20",
    }
    assert client.post("/equipment/upload", data=payload, follow_redirects=False).status_code == 303
    payload["solar__price"] = "0.25"
    assert client.post("/equipment/upload", data=payload, follow_redirects=False).status_code == 303
    rows = [p for p in equipment.catalogue()["solar"] if p["maker"] == "DupCo"]
    assert len(rows) == 1 and rows[0]["price"] == 0.25
