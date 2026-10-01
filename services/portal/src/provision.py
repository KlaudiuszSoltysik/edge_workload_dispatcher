from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from kubernetes.client.rest import ApiException

from . import k8s

api_router = APIRouter(prefix="/api/v1", tags=["provisioning"])
views_router = APIRouter(tags=["views"])
templates = Jinja2Templates(directory="services/portal/src/templates")


@views_router.get("/provision")
async def render_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="provision.html")


@api_router.get("/hardware/available")
def list_available_hardware():
    try:
        return {"available_hardware": k8s.get_available_hardware()}
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@api_router.get("/tasks")
def list_tasks():
    try:
        return k8s.get_all_tasks()
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@api_router.post("/tasks/provision")
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


@api_router.delete("/tasks/{task_name}")
def delete_task_endpoint(task_name: str):
    try:
        k8s.delete_task(task_name)
        return {"status": "Deleted", "task_name": task_name}
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))
