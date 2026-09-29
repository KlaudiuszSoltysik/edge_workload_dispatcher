import kubernetes
from kubernetes import client

kubernetes.config.load_kube_config()
core_api = client.CoreV1Api()
custom_api = client.CustomObjectsApi()

GROUP = "edge.platform"
VERSION = "v1"
PLURAL = "computetasks"

def get_available_hardware():
    nodes = core_api.list_node().items # type: ignore
    available = []

    for node in nodes: # type: ignore
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
        namespace="default",
        plural=PLURAL,
    )
    return tasks.get("items", []) # type: ignore

def create_task(gpu_model: str, gpu_count: int, disk_gb: int, image: str):
    task_body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ComputeTask",
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
        namespace="default",
        plural=PLURAL,
        body=task_body,
    )

def delete_task(task_name: str):
    return custom_api.delete_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace="default",
        plural=PLURAL,
        name=task_name,
    )