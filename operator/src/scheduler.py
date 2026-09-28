from kubernetes import client


def find_available_node(
    api: client.CoreV1Api, gpu_model: str, gpu_count: int
) -> str | None:
    nodes = api.list_node().items  # type: ignore

    for node in nodes:  # type: ignore
        labels = node.metadata.labels

        if labels.get("gpu-model") == gpu_model:
            free_gpus = int(labels.get("gpu-free", 0))

            if free_gpus >= gpu_count:
                return node.metadata.name

    return None


def allocate_gpu_on_node(
    api: client.CoreV1Api, node_name: str, allocated_count: int
) -> None:
    node = api.read_node(node_name)
    current_free = int(node.metadata.labels.get("gpu-free", 0))  # type: ignore
    new_free = current_free - allocated_count

    patch_body = {"metadata": {"labels": {"gpu-free": str(new_free)}}}

    api.patch_node(node_name, patch_body)


def release_gpu_on_node(
    api: client.CoreV1Api, node_name: str, released_count: int
) -> None:
    node = api.read_node(node_name)
    current_free = int(node.metadata.labels.get("gpu-free", 0))  # type: ignore
    new_free = current_free + released_count

    patch_body = {"metadata": {"labels": {"gpu-free": str(new_free)}}}

    api.patch_node(node_name, patch_body)
