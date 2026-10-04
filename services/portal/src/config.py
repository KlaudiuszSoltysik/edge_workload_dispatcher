from pathlib import Path

from prometheus_client import Counter

BASE_DIR = Path(__file__).resolve().parent

INFERENCE_REQUESTS_RECEIVED = Counter(
    "edge_inference_requests_received_total",
    "Total number of inference requests received from users",
)

INFERENCE_REQUESTS_ASSIGNED = Counter(
    "edge_inference_requests_assigned_total",
    "Total number of inference requests successfully sent to a worker pod",
)

INFERENCE_REQUESTS_COMPLETED = Counter(
    "edge_inference_requests_completed_total",
    "Total number of inference requests successfully completed and returned to user",
)
