import asyncio
import os
import time

import httpx
from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from .config import INFERENCE_REQUESTS_ASSIGNED, INFERENCE_REQUESTS_COMPLETED

try:
    config.load_incluster_config()
except ConfigException:
    config.load_kube_config()

core_api = client.CoreV1Api()
custom_api = client.CustomObjectsApi()

GROUP = "edge.platform"
VERSION = "v1"
PLURAL = "workspaces"
NAMESPACE = "default"


INFERENCE_SERVICE_URL = os.getenv("INFERENCE_SERVICE_URL", "http://127.0.0.1:8001")


def get_available_hardware():
    nodes = core_api.list_node().items  # type: ignore
    available = []

    for node in nodes:  # type: ignore
        labels = node.metadata.labels
        gpu_model = labels.get("gpu-model")
        free_gpus = int(labels.get("gpu-free", 0))

        if gpu_model is not None and free_gpus is not None:
            available.append({
                "node": node.metadata.name,
                "gpu_model": gpu_model,
                "gpu_free": free_gpus,
                "size": labels.get("size", "standard"),
            })
    return available


def get_all_tasks():
    tasks = custom_api.list_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=NAMESPACE,
        plural=PLURAL,
    )
    return tasks.get("items", [])  # type: ignore


def create_task(gpu_model: str, gpu_count: int, disk_gb: int, image: str):
    task_body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Workspace",
        "metadata": {"generateName": "client-task-"},
        "spec": {
            "gpuModel": gpu_model,
            "gpuCount": gpu_count,
            "diskGb": disk_gb,
            "image": image,
        },
    }
    return custom_api.create_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=NAMESPACE,
        plural=PLURAL,
        body=task_body,
    )


def delete_task(task_name: str):
    return custom_api.delete_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=NAMESPACE,
        plural=PLURAL,
        name=task_name,
    )


async def execute_inference(
    task_type: str, prompt: str, timeout: float = 45.0, max_wait_seconds: int = 90
) -> str:
    payload = {"task_type": task_type, "prompt": prompt}

    INFERENCE_REQUESTS_ASSIGNED.inc()

    start_time = time.time()
    last_err = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        while time.time() - start_time < max_wait_seconds:
            try:
                response = await client.post(INFERENCE_SERVICE_URL, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    INFERENCE_REQUESTS_COMPLETED.inc()
                    return data.get("result", "Empty response")
            except Exception as err:  # noqa: BLE001
                last_err = err
                await asyncio.sleep(1.5)

    INFERENCE_REQUESTS_COMPLETED.inc()
    return f"Inference worker unavailable (timed out after {max_wait_seconds}s): {last_err}"
