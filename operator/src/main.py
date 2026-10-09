import os
import threading
import time
import urllib.request

import kopf
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from prometheus_client import Gauge, start_http_server
from scheduler import (
    allocate_gpu_on_node,
    find_available_node,
    get_available_gpus_map,
    release_gpu_on_node,
)

GROUP = "edge.platform"
VERSION = "v1"
PLURAL = "workspaces"
SPECS_MAPPING = {"rtx-3060": {"cpu": 0.1, "ram": 0.1}, "l40s": {"cpu": 0.2, "ram": 0.2}}

NODE_GPU_FREE = Gauge(
    "edge_node_gpu_free", "Number of free GPUs on a node", ["node_name"]
)

QUEUE_THRESHOLD = int(os.getenv("QUEUE_THRESHOLD", "0"))
PORTAL_METRICS_URL = os.getenv(
    "PORTAL_METRICS_URL", "http://edge-portal.default.svc.cluster.local/metrics"
)


def background_metrics_loop():
    core_api = client.CoreV1Api()

    while True:
        try:
            nodes = core_api.list_node()
            for node in nodes.items:  # type: ignore
                gpu_free = int(node.metadata.labels.get("gpu-free", 0))
                NODE_GPU_FREE.labels(node_name=node.metadata.name).set(gpu_free)

        except Exception as err:  # noqa: BLE001
            print(f"Error updating metrics: {err}")

        time.sleep(10)


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()

    threading.Thread(target=start_http_server, args=(8000,), daemon=True).start()

    threading.Thread(target=background_metrics_loop, daemon=True).start()


# TODO: Add logging
@kopf.on.create(GROUP, VERSION, PLURAL)
def create_task(spec, name, namespace, logger, **kwargs):
    core_api = client.CoreV1Api()
    networking_api = client.NetworkingV1Api()

    gpu_model = spec["gpuModel"]
    gpu_count = spec["gpuCount"]
    disk_gb = spec["diskGb"]
    image = spec["image"]

    cpu_per_gpu = SPECS_MAPPING[gpu_model]["cpu"]
    total_cpu = cpu_per_gpu * gpu_count

    ram_per_gpu = SPECS_MAPPING[gpu_model]["ram"]
    total_ram_gb = ram_per_gpu * gpu_count

    target_node = find_available_node(core_api, gpu_model, gpu_count)
    if not target_node:
        raise kopf.TemporaryError(
            f"No nodes available for {gpu_count}x {gpu_model}", delay=10
        )

    target_node_obj = core_api.read_node(target_node)
    current_free_gpus = int(target_node_obj.metadata.labels.get("gpu-free", 0))  # type: ignore

    active_workers = core_api.list_namespaced_pod(
        namespace="default",
        label_selector=f"node={target_node}",
    )

    inference_workers = [
        p
        for p in active_workers.items  # type: ignore
        if p.metadata.labels.get("role") == "inference-worker"
    ]

    desired_max_workers = max(0, current_free_gpus - gpu_count)

    if len(inference_workers) > desired_max_workers:
        num_to_evict = len(inference_workers) - desired_max_workers
        workers_to_evict = inference_workers[:num_to_evict]

        for p in workers_to_evict:
            try:
                core_api.patch_namespaced_pod(
                    name=p.metadata.name,
                    namespace="default",
                    body={"metadata": {"labels": {"role": "draining"}}},
                )
                core_api.delete_namespaced_pod(
                    name=p.metadata.name, namespace="default"
                )
                logger.info(
                    f"Evicting {p.metadata.name} on {target_node} (draining {num_to_evict} required worker(s) for VIP task {name})"
                )
            except ApiException as err:
                if err.status != 404:
                    logger.error(f"Failed to evict worker {p.metadata.name}: {err}")

        raise kopf.TemporaryError(
            f"Node {target_node} is preparing resources (draining {num_to_evict} worker(s)). Waiting...",
            delay=3,
        )

    allocate_gpu_on_node(core_api, target_node, gpu_count)

    pvc_manifest = {
        "apiVersion": VERSION,
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"{name}-pvc",
            "labels": {"app": f"{name}-workload"},
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": f"{disk_gb}Gi"}},
        },
    }

    pod_manifest = {
        "apiVersion": VERSION,
        "kind": "Pod",
        "metadata": {
            "name": f"{name}-workload",
            "labels": {"app": f"{name}-workload"},
        },
        "spec": {
            "priorityClassName": "vip-provisioning",
            "automountServiceAccountToken": False,
            "nodeSelector": {"kubernetes.io/hostname": target_node},
            "initContainers": [
                {
                    "name": "model-loader",
                    "image": "edge-models:latest",
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sh", "-c", "cp -a /models/. /opt/models/"],
                    "volumeMounts": [
                        {"name": "model-storage", "mountPath": "/opt/models"}
                    ],
                }
            ],
            "containers": [
                {
                    "name": "workspace",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "ports": [{"containerPort": 7681}],
                    "resources": {
                        "requests": {
                            "cpu": total_cpu,
                            "memory": f"{total_ram_gb}Gi",
                            "ephemeral-storage": "1Gi",
                        },
                        "limits": {
                            "cpu": total_cpu,
                            "memory": f"{total_ram_gb}Gi",
                            "ephemeral-storage": "1Gi",
                        },
                    },
                    "securityContext": {
                        "privileged": False,
                        "allowPrivilegeEscalation": False,
                        "capabilities": {
                            "drop": ["ALL"],
                            "add": [
                                "CHOWN",
                                "DAC_OVERRIDE",
                                "FOWNER",
                                "FSETID",
                                "KILL",
                                "SETGID",
                                "SETUID",
                                "NET_BIND_SERVICE",
                            ],
                        },
                    },
                    "volumeMounts": [
                        {"name": "persistent-storage", "mountPath": "/workspace"},
                        {
                            "name": "model-storage",
                            "mountPath": "/opt/models",
                            "readOnly": True,
                        },
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "persistent-storage",
                    "persistentVolumeClaim": {"claimName": f"{name}-pvc"},
                },
                {
                    "name": "model-storage",
                    "emptyDir": {},
                },
            ],
        },
    }

    service_manifest = {
        "apiVersion": VERSION,
        "kind": "Service",
        "metadata": {"name": f"{name}-svc"},
        "spec": {
            "selector": {"app": f"{name}-workload"},
            "ports": [{"port": 7681, "targetPort": 7681}],
        },
    }

    ingress_manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": f"{name}-ingress", "labels": {"app": f"{name}-workload"}},
        "spec": {
            "ingressClassName": "nginx",
            "rules": [
                {
                    "host": f"{name}.localhost",
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": f"{name}-svc",
                                        "port": {"number": 7681},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }

    # TODO (Production):
    # 1. Replace Kind CNI with Cilium or Calico to enforce NetworkPolicy egress filtering.
    # 2. Extract cluster CIDR and host VPC CIDR from ConfigMap / env variables instead of hardcoded RFC 1918 subnets.
    # 3. Consider using CiliumNetworkPolicy for DNS L7 filtering (fqdn matching) to prevent DNS tunneling.
    network_policy_manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{name}-netpol", "labels": {"app": f"{name}-workload"}},
        "spec": {
            "podSelector": {"matchLabels": {"app": f"{name}-workload"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"ports": [{"port": 7681, "protocol": "TCP"}]}],
            "egress": [
                {
                    "ports": [
                        {"port": 53, "protocol": "UDP"},
                        {"port": 53, "protocol": "TCP"},
                    ]
                },
                {
                    "to": [
                        {
                            "ipBlock": {
                                "cidr": "0.0.0.0/0",
                                "except": [
                                    "10.0.0.0/8",
                                    "172.16.0.0/12",
                                    "192.168.0.0/16",
                                    "169.254.169.254/32",
                                ],
                            }
                        }
                    ]
                },
            ],
        },
    }

    kopf.adopt(pvc_manifest)
    kopf.adopt(pod_manifest)
    kopf.adopt(service_manifest)
    kopf.adopt(ingress_manifest)
    kopf.adopt(network_policy_manifest)

    core_api.create_namespaced_persistent_volume_claim(
        namespace=namespace, body=pvc_manifest
    )
    core_api.create_namespaced_pod(namespace=namespace, body=pod_manifest)
    core_api.create_namespaced_service(namespace=namespace, body=service_manifest)
    networking_api.create_namespaced_ingress(namespace=namespace, body=ingress_manifest)
    networking_api.create_namespaced_network_policy(
        namespace=namespace, body=network_policy_manifest
    )

    return {
        "status": "Scheduling",
        "allocatedNode": target_node,
        "webTerminalUrl": f"http://{name}.localhost",
    }


@kopf.on.delete(GROUP, VERSION, PLURAL)
def delete_task(spec, name, namespace, logger, **kwargs):
    core_api = client.CoreV1Api()
    gpu_count = spec.get("gpuCount", 1)

    try:
        pod = core_api.read_namespaced_pod(name=f"{name}-workload", namespace=namespace)
        allocated_node = pod.spec.node_name  # type: ignore

        if allocated_node:
            logger.info(f"Releasing {gpu_count} GPU(s) on node {allocated_node}")
            release_gpu_on_node(core_api, allocated_node, gpu_count)
    except ApiException as err:
        if err.status == 404:
            logger.warning(
                f"Pod {name}-workload was already deleted, skipping GPU node lookup"
            )
        else:
            logger.error(f"Unexpected API error during deletion: {err}")
    except Exception as err:  # noqa: BLE001
        logger.error(f"Unhandled error during task deletion: {err}")


@kopf.on.event("", VERSION, "pods", labels={"app": kopf.PRESENT})
def sync_pod_status(event, body, logger, **kwargs):
    owner_references = body.get("metadata", {}).get("ownerReferences", [])
    if not owner_references:
        return

    task_name = None
    for ref in owner_references:
        if ref.get("kind") == "Workspace":
            task_name = ref.get("name")
            break

    if not task_name:
        return

    pod_phase = body.get("status", {}).get("phase", "Unknown")
    namespace = body["metadata"]["namespace"]

    custom_api = client.CustomObjectsApi()

    try:
        task = custom_api.get_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=task_name,
        )
        current_status = task.get("status", {}).get("create_task", {})  # type: ignore

        if current_status.get("status") != pod_phase:
            patch = {"status": {"create_task": {**current_status, "status": pod_phase}}}
            custom_api.patch_namespaced_custom_object_status(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=task_name,
                body=patch,
            )
    except ApiException as err:
        if err.status != 404:
            logger.error(f"Failed to sync status for {task_name}: {err}")


def get_inference_queue_depth() -> int:
    try:
        req = urllib.request.Request(PORTAL_METRICS_URL)
        with urllib.request.urlopen(req, timeout=1.5) as response:
            content = response.read().decode("utf-8")
            received = 0
            completed = 0
            for line in content.splitlines():
                if line.startswith("edge_inference_requests_received_total"):
                    received = int(float(line.split()[-1]))
                elif line.startswith("edge_inference_requests_completed_total"):
                    completed = int(float(line.split()[-1]))
            return max(0, received - completed)
    except Exception:  # noqa: BLE001
        return 0


def create_worker_manifest(node_name: str, pod_name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {
                "role": "inference-worker",
                "node": node_name,
            },
        },
        "spec": {
            "priorityClassName": "batch-priority",
            "restartPolicy": "Always",
            "nodeSelector": {"kubernetes.io/hostname": node_name},
            "initContainers": [
                {
                    "name": "model-loader",
                    "image": "edge-models:latest",
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sh", "-c", "cp -a /models/. /opt/models/"],
                    "volumeMounts": [
                        {
                            "name": "model-storage",
                            "mountPath": "/opt/models",
                        }
                    ],
                }
            ],
            "containers": [
                {
                    "name": "inference-daemon",
                    "image": "python:3.11-slim",
                    "command": ["python3", "/scripts/runner.py"],
                    "ports": [{"containerPort": 8000}],
                    "volumeMounts": [
                        {
                            "name": "model-storage",
                            "mountPath": "/opt/models",
                            "readOnly": True,
                        },
                        {
                            "name": "runner-script-vol",
                            "mountPath": "/scripts",
                            "readOnly": True,
                        },
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "model-storage",
                    "emptyDir": {},
                },
                {
                    "name": "runner-script-vol",
                    "configMap": {"name": "inference-runner-script"},
                },
            ],
        },
    }


@kopf.timer("", VERSION, "nodes", interval=5.0)
def reconcile_inference_workers(logger, **kwargs):
    core_api = client.CoreV1Api()
    available_gpus = get_available_gpus_map(core_api)
    queue_depth = get_inference_queue_depth()
    logger.info(f"Current inference queue depth: {queue_depth}")

    pods = core_api.list_namespaced_pod(
        namespace="default", label_selector="role=inference-worker"
    )

    node_workers = {}
    for pod in pods.items:  # type: ignore
        if pod.metadata.deletion_timestamp:
            continue
        node = pod.metadata.labels.get("node")
        if node:
            node_workers.setdefault(node, []).append(pod.metadata.name)

    nodes = core_api.list_node().items  # type: ignore

    warm_nodes = []
    cold_nodes = []
    desired_map = {}

    for node in nodes:  # type: ignore
        node_name = node.metadata.name
        gpu_total = int(node.metadata.labels.get("gpu-total", 0))

        if gpu_total == 0:
            continue

        free_count = available_gpus.get(node_name, 0)
        if free_count == 0:
            desired_map[node_name] = 0
            continue

        if free_count < gpu_total:
            warm_nodes.append((node_name, free_count, gpu_total))
        else:
            cold_nodes.append((node_name, free_count, gpu_total))

    warm_capacity = 0
    for node_name, free_count, _ in warm_nodes:
        desired_map[node_name] = free_count
        warm_capacity += free_count

    remaining_queue = max(0, queue_depth - warm_capacity)

    cold_nodes.sort(key=lambda x: x[2])

    for node_name, free_count, gpu_total in cold_nodes:
        if remaining_queue > QUEUE_THRESHOLD:
            allocated = min(remaining_queue, free_count)
            desired_map[node_name] = allocated
            remaining_queue -= allocated
            logger.info(
                f"Waking cold node {node_name} (gpu-total: {gpu_total}) with {allocated} worker(s). Remaining queue: {remaining_queue}"
            )
        else:
            desired_map[node_name] = 0

    for node in nodes:  # type: ignore
        node_name = node.metadata.name
        if node_name not in desired_map:
            continue

        desired_workers = desired_map[node_name]
        current_pods = node_workers.get(node_name, [])

        if len(current_pods) > desired_workers:
            excess = len(current_pods) - desired_workers
            for _ in range(excess):
                pod_to_delete = current_pods.pop()
                try:
                    core_api.patch_namespaced_pod(
                        name=pod_to_delete,
                        namespace="default",
                        body={"metadata": {"labels": {"role": "draining"}}},
                    )
                    core_api.delete_namespaced_pod(
                        name=pod_to_delete, namespace="default"
                    )
                    logger.info(
                        f"Draining worker {pod_to_delete} on {node_name} (Target: {desired_workers}, Active: {len(current_pods)})"
                    )
                except ApiException as err:
                    if err.status != 404:
                        logger.error(f"Failed to evict worker {pod_to_delete}: {err}")

        elif len(current_pods) < desired_workers:
            existing_names = set(current_pods)
            needed = desired_workers - len(current_pods)
            created = 0
            idx = 0

            while created < needed:
                pod_name = f"inference-worker-{node_name}-{idx}"
                idx += 1
                if pod_name in existing_names:
                    continue

                pod_manifest = create_worker_manifest(node_name, pod_name)
                try:
                    core_api.create_namespaced_pod(
                        namespace="default", body=pod_manifest
                    )
                    logger.info(
                        f"Provisioned inference worker {pod_name} on {node_name}"
                    )
                    existing_names.add(pod_name)
                    created += 1
                except ApiException as err:
                    if err.status != 409:
                        logger.error(f"Failed to create {pod_name}: {err}")
