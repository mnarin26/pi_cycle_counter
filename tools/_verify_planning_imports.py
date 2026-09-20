from app.api.routers import planning, products, molds
from app.services.planning.plan_engine import build_plan
from app.db.session import init_db
print("imports-ok", planning.router, products.router)
init_db()
print("init-db-ok")
