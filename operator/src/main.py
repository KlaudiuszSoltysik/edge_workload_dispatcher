import kopf
import kubernetes
from kubernetes import client
from scheduler import allocate_gpu_on_node, find_available_node

GPU_RAM_MAPPING = {"rtx-3060": 2, "l40s": 4}


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    kubernetes.config.load_kube_config()


@kopf.on.create("edge.ks.ops", "v1", "computetasks")
def create_task(spec, name, namespace, logger, **kwargs):
    core_api = client.CoreV1Api()

    gpu_model = spec.get("gpuModel")
    gpu_count = spec.get("gpuCount", 1)
    disk_gb = spec.get("diskGb", 2)
    image = spec.get("image", "ubuntu-ssh:latest")

    ram_per_gpu = GPU_RAM_MAPPING.get(gpu_model, 8)
    total_ram_gb = ram_per_gpu * gpu_count

    target_node = find_available_node(core_api, gpu_model, gpu_count)
    if not target_node:
        raise kopf.TemporaryError(
            f"No nodes available for {gpu_count}x {gpu_model}", delay=10
        )

    allocate_gpu_on_node(core_api, target_node, gpu_count)

    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"{name}-workload",
            "labels": {"app": f"workload-{name}"},
        },
        "spec": {
            "nodeName": target_node,
            "containers": [
                {
                    "name": "workspace",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "ports": [{"containerPort": 22}],
                    "resources": {
                        "requests": {
                            "memory": f"{total_ram_gb}Gi",
                            "ephemeral-storage": f"{disk_gb}Gi",
                        },
                        "limits": {
                            "memory": f"{total_ram_gb}Gi",
                            "ephemeral-storage": f"{disk_gb}Gi",
                        },
                    },
                }
            ],
        },
    }

    service_manifest = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": f"{name}-ssh-svc"},
        "spec": {
            "type": "NodePort",
            "selector": {"app": f"workload-{name}"},
            "ports": [{"port": 22, "targetPort": 22}],
        },
    }

    kopf.adopt(pod_manifest)
    kopf.adopt(service_manifest)

    core_api.create_namespaced_pod(namespace=namespace, body=pod_manifest)

    svc = core_api.create_namespaced_service(namespace=namespace, body=service_manifest)
    node_port = svc.spec.ports[0].node_port

    return {
        "status": "Running",
        "allocatedNode": target_node,
        "sshCommand": f"ssh root@localhost -p {node_port} (hasło: edge2026)",
    }
