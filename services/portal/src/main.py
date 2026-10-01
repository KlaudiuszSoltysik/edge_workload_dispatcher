from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from kubernetes.client.rest import ApiException

from . import k8s

app = FastAPI(title="Edge Workload Portal", version="1.0.0")
templates = Jinja2Templates(directory="services/portal/src/templates")


@app.get("/")
def render_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/v1/hardware/available")
def list_available_hardware():
    try:
        return {"available_hardware": k8s.get_available_hardware()}
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@app.get("/api/v1/tasks")
def list_tasks():
    try:
        return k8s.get_all_tasks()
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@app.post("/api/v1/tasks/provision")
def request_task(
    gpu_model: str,
    gpu_count: int = 1,
    disk_gb: int = 5,
    image: str = "ubuntu-ssh:latest",
):
    try:
        created = k8s.create_task(gpu_model, gpu_count, disk_gb, image)
        return {"status": "Submitted", "task_name": created["metadata"]["name"]}  # type: ignore
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@app.delete("/api/v1/tasks/{task_name}")
def delete_task_endpoint(task_name: str):
    try:
        k8s.delete_task(task_name)
        return {"status": "Deleted", "task_name": task_name}
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="chat.html", context={"request": request, "result": None}
    )


@app.post("/chat", response_class=HTMLResponse)
async def handle_prompt(
    request: Request, task_type: str = Form(...), prompt: str = Form(...)
):
    result = k8s.submit_and_wait_for_task(task_type, prompt)

    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"request": request, "result": result, "last_prompt": prompt},
    )
