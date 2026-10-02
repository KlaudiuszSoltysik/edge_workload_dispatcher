import kopf
import kubernetes
from kubernetes import client
from kubernetes.client.rest import ApiException
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


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    kubernetes.config.load_kube_config()


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
                    "hostPath": {
                        "path": "/opt/models",
                        "type": "DirectoryOrCreate",
                    },
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


RUNNER_SCRIPT = """
import http.server
import json
import subprocess

class InferenceHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}

        task_type = data.get('task_type', 'llm')
        prompt = data.get('prompt', '')
        script_path = f"/opt/models/{task_type}_model.sh"

        try:
            cmd = ["/bin/bash", script_path, prompt]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = proc.stdout.strip()
        except subprocess.CalledProcessError as err:
            output = f"Execution failed: {err.stderr}"

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        response = json.dumps({'result': output})
        self.wfile.write(response.encode('utf-8'))

if __name__ == '__main__':
    server = http.server.HTTPServer(('0.0.0.0', 8000), InferenceHandler)
    server.serve_forever()
"""


@kopf.timer("", VERSION, "nodes", interval=5.0)
def reconcile_inference_workers(logger, **kwargs):
    core_api = client.CoreV1Api()
    available_gpus = get_available_gpus_map(core_api)

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

    nodes = core_api.list_node()
    for node in nodes.items:  # type: ignore
        node_name = node.metadata.name
        free_count = available_gpus.get(node_name, 0)
        current_pods = node_workers.get(node_name, [])

        if len(current_pods) > free_count:
            excess = len(current_pods) - free_count
            for i in range(excess):
                pod_to_delete = current_pods.pop()
                try:
                    core_api.delete_namespaced_pod(
                        name=pod_to_delete,
                        namespace="default",
                        body=client.V1DeleteOptions(grace_period_seconds=0),
                    )
                    logger.info(
                        f"Evicted worker {pod_to_delete} (GPU preempted on {node_name})"
                    )
                except ApiException as err:
                    if err.status != 404:
                        logger.error(f"Failed to delete worker {pod_to_delete}: {err}")

        elif len(current_pods) < free_count:
            for i in range(len(current_pods), free_count):
                pod_name = f"inference-worker-{node_name}-{i}"
                if pod_name in current_pods:
                    continue

                pod_manifest = {
                    "apiVersion": VERSION,
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
                        "containers": [
                            {
                                "name": "inference-daemon",
                                "image": "python:3.11-slim",
                                "command": ["python3", "-c", RUNNER_SCRIPT],
                                "ports": [{"containerPort": 8000}],
                                "volumeMounts": [
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
                                "name": "model-storage",
                                "hostPath": {
                                    "path": "/opt/models",
                                    "type": "Directory",
                                },
                            }
                        ],
                    },
                }

                try:
                    core_api.create_namespaced_pod(
                        namespace="default", body=pod_manifest
                    )
                    logger.info(f"Created worker {pod_name} on {node_name}")
                except ApiException as err:
                    if err.status != 409:
                        logger.error(f"Failed to create {pod_name}: {err}")
