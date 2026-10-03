terraform {
  required_providers {
    kind = {
      source  = "tehcyx/kind"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
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
      
      kubeadm_config_patches = [
        <<-EOT
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
        EOT
      ]

      extra_port_mappings {
        container_port = 80
        host_port      = 80
        protocol       = "TCP"
      }
    }

    node {
      role = "worker"
      labels = {
        "size"      = "test"
        "gpu-model" = "rtx-3060"
        "gpu-total" = "1"
        "gpu-free"  = "1"
      }
      extra_mounts {
        host_path      = "./models"
        container_path = "/opt/models"
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
      extra_mounts {
        host_path      = "./models"
        container_path = "/opt/models"
      }
    }

    node {
      role = "worker"
      labels = {
        "size"      = "M"
        "gpu-model" = "l40s"
        "gpu-total" = "8"
        "gpu-free"  = "8"
      }
      extra_mounts {
        host_path      = "./models"
        container_path = "/opt/models"
      }
    }

    node {
      role = "worker"
      labels = {
        "size"      = "L"
        "gpu-model" = "l40s"
        "gpu-total" = "16"
        "gpu-free"  = "16"
      }
      extra_mounts {
        host_path      = "./models"
        container_path = "/opt/models"
      }
    }
  }
}

provider "helm" {
  kubernetes {
    config_path    = "~/.kube/config"
    config_context = "kind-edge-workload-dispatcher"
  }
}

resource "helm_release" "ingress_nginx" {
  name             = "ingress-nginx"
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  version          = "4.10.1"
  namespace        = "ingress-nginx"
  create_namespace = true

  set {
    name  = "controller.admissionWebhooks.enabled"
    value = "false"
  }

  set {
    name  = "controller.nodeSelector.ingress-ready"
    value = "true"
    type  = "string"
  }

  set {
    name  = "controller.tolerations[0].key"
    value = "node-role.kubernetes.io/control-plane"
  }

  set {
    name  = "controller.tolerations[0].operator"
    value = "Exists"
  }

  set {
    name  = "controller.tolerations[0].effect"
    value = "NoSchedule"
  }

  set {
    name  = "controller.hostPort.enabled"
    value = "true"
  }

  set {
    name  = "controller.service.type"
    value = "NodePort"
  }
}