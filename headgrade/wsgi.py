"""Production entry point:  gunicorn -b 0.0.0.0:8080 headgrade.wsgi:app
Set HEADGRADE_DEMO=1 to serve a demo site of fictional companies instead of the real database."""
import os

from .site import create_app

if os.getenv("HEADGRADE_DEMO") == "1":
    from .demo import demo_engine
    app = create_app(demo_engine(), demo=True)
else:
    app = create_app()
