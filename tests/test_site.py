import json
from datetime import date, datetime

import pytest
from sqlalchemy import select

import test_weekly as tw
from headgrade import db, pipeline, prices, site, weekly


@pytest.fixture
def client(tmp_path):
    eng = db.make_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(eng)
    with db.session_scope(eng) as s:
        co = tw.add_company(s)
        co.primary_metals = "au"
        prices.set_price(s, "BTG", 5.12, "USD", date(2026, 10, 9))
        s.add(db.PriceDeck(name="spot", as_of=date(2026, 10, 9), prices_json='{"au": 4367.35}'))
        weekly.process_company(s, co, tw.fake_edgar(tw.SUB), tw.Reader(tw.facts()), tw.Reader(tw.facts()), today=date(2026, 10, 11))
        # a second company whose only snapshot is a DRAFT: must never appear publicly
        d = db.Company(name="Draft Only Mining", ticker="DRFT", stage="developer", primary_metals="cu", currency="USD")
        s.add(d)
        s.flush()
        card = json.loads(s.scalars(select(db.Snapshot)).first().card_json)
        card["mode"] = "draft"
        card["company"].update(name=d.name, ticker="DRFT")
        s.add(db.Snapshot(company_id=d.id, card_json=pipeline.card_json(card), summary_json=json.dumps(pipeline.summarize(card)), published=False,
                          quality_score=70, value_score=70))
        # a third, published, copper developer for filter tests
        k = json.loads(s.scalars(select(db.Snapshot)).first().card_json)
        e = db.Company(name="Copper Dev <b>Corp</b>", ticker="CDV", stage="developer", primary_metals="cu", currency="USD")
        s.add(e)
        s.flush()
        k["company"].update(name=e.name, ticker="CDV", stage="developer", metals=["cu"])
        k["grade"].update(quality_score=40, quality_grade="D", value_score=90, value_grade="A")
        s.add(db.Snapshot(company_id=e.id, card_json=pipeline.card_json(k), summary_json=json.dumps(pipeline.summarize(k)), published=True,
                          quality_score=40, value_score=90, taken_at=datetime(2026, 10, 12)))
    return site.create_app(eng).test_client()


def test_every_page_renders(client):
    for u in ("/", "/companies", "/methodology", "/about", "/disclosures", "/company/BTG", "/company/btg", "/healthz", "/robots.txt", "/sitemap.xml"):
        assert client.get(u).status_code == 200, u
    assert client.get("/company/NOPE").status_code == 404


def test_only_published_cards_are_public(client):
    assert client.get("/company/DRFT").status_code == 404
    page = client.get("/companies").get_data(as_text=True)
    assert "B2Gold" in page and "Draft Only" not in page
    assert "/company/DRFT" not in client.get("/sitemap.xml").get_data(as_text=True) and "/company/BTG" in client.get("/sitemap.xml").get_data(as_text=True)


def test_filters_sort_and_escaping(client):
    cu = client.get("/companies?metal=cu").get_data(as_text=True)
    assert "CDV" in cu and "B2Gold" not in cu
    assert "B2Gold" in client.get("/companies?stage=producer").get_data(as_text=True)
    assert "CDV" not in client.get("/companies?q=b2gold").get_data(as_text=True)
    byval = client.get("/companies?sort=value").get_data(as_text=True)
    assert byval.index("CDV") < byval.index("BTG")                  # 90 before the producer's score
    assert "<b>Corp</b>" not in cu and "&lt;b&gt;Corp" in cu       # names are escaped, never trusted as HTML
    assert client.get("/companies?q=%25%25_&page=zzz&sort=bogus").status_code == 200


def test_company_page_has_card_scorecard_and_no_admin_tools(client):
    p = client.get("/company/BTG").get_data(as_text=True)
    assert "Project scorecard" in p and "Sources" in p and "Quality" in p and "HeadGrade" in p
    assert "streamlit" not in p.lower()
    for bad in ("/admin", "/review", "/queue"):
        assert client.get(bad).status_code == 404


def test_map_and_headers(client):
    home = client.get("/")
    assert "class='map'" in home.get_data(as_text=True) and home.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/brand/headgrade-icon.svg").status_code == 200
    assert client.get("/brand/../README.md").status_code == 404


def test_demo_mode_builds_fictional_site_and_labels_it(tmp_path, monkeypatch):
    from headgrade import config, demo
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    c = site.create_app(demo.demo_engine(), demo=True).test_client()
    home = c.get("/").get_data(as_text=True)
    assert "Demo mode" in home and "noindex" in home and "FICTIONAL" in c.get("/companies").get_data(as_text=True)
    assert config.DATA_DIR == tmp_path
    assert c.get("/company/EXM").status_code == 200
