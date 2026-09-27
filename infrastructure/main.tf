terraform {
  required_providers {
    kind = {
      source  = "tehcyx/kind"
    }
  }
}

provider "kind" {}

resource "kind_cluster" "dispatcher_cluster" {
  name           = "edge-workload-dispatcher"
  node_image     = "kindest/node:v1.30.0"
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
    }

    node {
      role = "worker"
      labels = {
        "size"      = "test"
        "gpu-model" = "rtx-3060"
        "gpu-total" = "1"
        "gpu-free"  = "1"
      }
    }

    node {
      role = "worker"
      labels = {
        "size"      = "S"
        "gpu-model" = "l40s"
        "gpu-total" = "4"
        "gpu-free"  = "4"
      }
    }
  }
}