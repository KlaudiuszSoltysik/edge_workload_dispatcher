from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import k8s
from .config import BASE_DIR

router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="chat.html", context={"request": request, "result": None}
    )


@router.post("/chat", response_class=HTMLResponse)
async def handle_prompt(
    request: Request, task_type: str = Form(...), prompt: str = Form(...)
):
    result = k8s.execute_inference(task_type, prompt)
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"result": result, "last_prompt": prompt},
    )
