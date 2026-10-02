from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .chat import router as chat_router
from .config import BASE_DIR
from .provision import api_router as provision_api_router
from .provision import views_router as provision_views_router

app = FastAPI(title="Edge Workload Portal", version="1.0.0")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(chat_router)
app.include_router(provision_api_router)
app.include_router(provision_views_router)
