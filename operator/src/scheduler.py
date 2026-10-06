from kubernetes import client


def find_available_node(
    core_api: client.CoreV1Api, gpu_model: str, required_gpus: int
) -> str | None:
    nodes = core_api.list_node().items  # type: ignore
    eligible_nodes = []

    for node in nodes:  # type: ignore
        labels = node.metadata.labels
        node_gpu_model = labels.get("gpu-model")
        gpu_total = int(labels.get("gpu-total", 0))
        gpu_free = int(labels.get("gpu-free", 0))

        if node_gpu_model != gpu_model:
            continue
        if gpu_free < required_gpus:
            continue

        allocated_gpus = gpu_total - gpu_free
        utilization_score = allocated_gpus / gpu_total if gpu_total > 0 else 0.0

        eligible_nodes.append({
            "name": node.metadata.name,
            "score": utilization_score,
            "gpu_free": gpu_free,
        })

    if not eligible_nodes:
        return None

    eligible_nodes.sort(
        key=lambda item: (item["score"], -item["gpu_free"]), reverse=True
    )

    return eligible_nodes[0]["name"]


def allocate_gpu_on_node(
    api: client.CoreV1Api, node_name: str, allocated_count: int
) -> None:
    node = api.read_node(node_name)
    current_free = int(node.metadata.labels["gpu-free"])  # type: ignore
    new_free = current_free - allocated_count

    patch_body = {"metadata": {"labels": {"gpu-free": str(new_free)}}}

    api.patch_node(node_name, patch_body)


def release_gpu_on_node(
    api: client.CoreV1Api, node_name: str, released_count: int
) -> None:
    node = api.read_node(node_name)
    current_free = int(node.metadata.labels["gpu-free"])  # type: ignore
    new_free = current_free + released_count

    patch_body = {"metadata": {"labels": {"gpu-free": str(new_free)}}}

    api.patch_node(node_name, patch_body)


def get_available_gpus_map(core_api: client.CoreV1Api) -> dict:
    available_gpus = {}
    nodes = core_api.list_node()

    for node in nodes.items:  # type: ignore
        if node.spec.unschedulable:
            continue

        labels = node.metadata.labels or {}
        free_count = int(labels.get("gpu-free", 0))

        if free_count > 0:
            available_gpus[node.metadata.name] = free_count

    return available_gpus
