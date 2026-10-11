import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from examples.demo_data import build
from minecards import db, pipeline
from minecards.render import render_html
from minecards.valuation import Market

out = sys.argv[1] if len(sys.argv) > 1 else "demo_card.html"
engine = db.make_engine("sqlite:///:memory:")
db.init_db(engine)
with db.session_scope(engine) as s:
    co = build(s)
    card = pipeline.build_card(s, co, pipeline.latest_deck(s), Market(2.50, "USD", date(2026, 10, 9)), publish=True, today=date(2026, 10, 9))
    Path(out).write_text(render_html(card, ["First card for this company (fictional demo)."]))
print("wrote", out)
