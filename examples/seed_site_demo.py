"""Fill a database with FICTIONAL companies so the public site can be previewed (no network, no API key).
Usage: python examples/seed_site_demo.py <output_dir>   then:  DATABASE_URL=sqlite:///<output_dir>/demo.db python -m headgrade.cli serve"""
from __future__ import annotations

import copy
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select

import demo_pipeline
from headgrade import db, grading, pipeline, weekly

FAKE = [("Aurora Gold Corp", "AUGC", "developer", "au", 71, 78, "Canada"), ("Redstone Copper Ltd", "RSCU", "producer", "cu", 66, 52, "Chile"),
        ("Northfield Uranium Inc", "NFU", "developer", "u3o8", 58, 66, "Saskatchewan"), ("Silvermine Resources", "SVMR", "producer", "ag", 49, 71, "Mexico"),
        ("Kestrel Nickel Corp", "KNC", "developer", "ni", 74, 44, "Western Australia"), ("Harbour Gold Mines", "HGM", "producer", "au", 83, 38, "Ontario"),
        ("Delta Exploration", "DLTX", "explorer", "au", 42, None, "Nevada")]


def main(out: Path):
    summary, engine = demo_pipeline.build(out)
    with db.session_scope(engine) as s:
        co = s.scalar(select(db.Company))
        card, status, _ = weekly.build_company_card(s, co, date(2026, 10, 11), publish=True, save=True)
        base = card
        for i, (name, tk, stage, metal, q, v, j) in enumerate(FAKE):
            c = db.Company(name=f"{name} (FICTIONAL)", ticker=tk, stage=stage, primary_metals=metal, jurisdiction=j, currency="USD", exchange="NYSE American")
            s.add(c)
            s.flush()
            k = copy.deepcopy(base)
            k["company"].update(name=c.name, ticker=tk, stage=stage, metals=[metal], jurisdiction=j)
            k["disclosures"] = {"sponsored": False, "owner_holds_position": False}
            g = k["grade"]
            g.update(quality_score=q, quality_grade=grading.letter(q), value_score=v, value_grade=grading.letter(v))
            g["quadrant"] = "Illustrative data only"
            k["market"]["price"] = round(1.5 + i * 0.7, 2)
            for t in (timedelta(days=14), timedelta(days=7), timedelta(0)):   # a little history
                k2 = copy.deepcopy(k)
                snap = db.Snapshot(company_id=c.id, price=k2["market"]["price"], price_date=date(2026, 10, 9), card_json=pipeline.card_json(k2),
                                   quality_score=q - (3 if t else 0), value_score=(v - (4 if t else 0)) if v is not None else None, published=True,
                                   summary_json=json.dumps(pipeline.summarize(k2)), taken_at=datetime(2026, 10, 11) - t)
                s.add(snap)
    print("seeded", out / "demo.db")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo_out"))
