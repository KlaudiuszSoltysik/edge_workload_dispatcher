import kubernetes
from fastapi import FastAPI, HTTPException
from kubernetes import client
from kubernetes.client.rest import ApiException

app = FastAPI(title="Edge Workload Portal", version="1.0.0")

kubernetes.config.load_kube_config()
core_api = client.CoreV1Api()
custom_api = client.CustomObjectsApi()


@app.get("/api/v1/hardware/available")
def list_available_hardware():
    nodes = core_api.list_node().items  # type: ignore
    available = []

    for node in nodes:  # type: ignore
        labels = node.metadata.labels
        gpu_model = labels.get("gpu-model")
        free_gpus = int(labels.get("gpu-free", 0))

        if gpu_model and gpu_model != "none" and free_gpus > 0:
            available.append({
                "node": node.metadata.name,
                "gpu_model": gpu_model,
                "gpu_free": free_gpus,
                "size": labels.get("size", "standard"),
            })

    return {"available_hardware": available}


@app.post("/api/v1/tasks/provision")
def request_task(
    gpu_model: str,
    gpu_count: int = 1,
    disk_gb: int = 5,
    image: str = "ubuntu-ssh:latest",
):
    task_body = {
        "apiVersion": "edge.platform/v1",
        "kind": "ComputeTask",
        "metadata": {"generateName": "client-task-"},
        "spec": {
            "gpuModel": gpu_model,
            "gpuCount": gpu_count,
            "diskGb": disk_gb,
            "image": image,
        },
    }

    try:
        created_task = custom_api.create_namespaced_custom_object(
            group="edge.platform",
            version="v1",
            namespace="default",
            plural="computetasks",
            body=task_body,
        )
        return {"status": "Submitted", "task_name": created_task["metadata"]["name"]}  # type: ignore
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))


@app.delete("/api/v1/tasks/{task_name}")
def delete_task(task_name: str):
    try:
        custom_api.delete_namespaced_custom_object(
            group="edge.platform",
            version="v1",
            namespace="default",
            plural="computetasks",
            name=task_name,
        )
        return {"status": "Deleted", "task_name": task_name}
    except ApiException as err:
        raise HTTPException(status_code=int(err.status or 500), detail=str(err))
