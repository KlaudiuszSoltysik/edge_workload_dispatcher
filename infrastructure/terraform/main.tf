terraform {
  required_providers {
    kind = {
      source  = "tehcyx/kind"
    }
    helm = {
      source  = "hashicorp/helm"
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

resource "helm_release" "prometheus_stack" {
  name             = "prometheus"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  namespace        = "monitoring"
  create_namespace = true

  timeout = 9999 

  depends_on = [helm_release.ingress_nginx]

  set {
    name  = "alertmanager.enabled"
    value = "false"
  }

  set {
    name  = "grafana.ingress.enabled"
    value = "true"
  }
  set {
    name  = "grafana.ingress.ingressClassName"
    value = "nginx"
  }
  set {
    name  = "grafana.ingress.hosts[0]"
    value = "grafana.localhost"
  }
}

resource "helm_release" "loki" {
  name             = "loki"
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "loki"
  namespace        = "monitoring"
  create_namespace = true

  timeout = 9999

  depends_on = [helm_release.prometheus_stack]

  values = [
    yamlencode({
      deploymentMode = "SingleBinary"
      loki = {
        auth_enabled  = false
        useTestSchema = true
        commonConfig = {
          replication_factor = 1
        }
        storage = {
          type = "filesystem"
        }
      }
      singleBinary = {
        replicas = 1
      }
      read = {
        replicas = 0
      }
      write = {
        replicas = 0
      }
      backend = {
        replicas = 0
      }
    })
  ]
}

resource "helm_release" "alloy" {
  name             = "alloy"
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "alloy"
  namespace        = "monitoring"
  create_namespace = true

  timeout = 9999

  depends_on = [helm_release.loki]

  values = [
    <<-EOT
    alloy:
      configMap:
        content: |-
          // 1. Configure where to send logs (Loki)
          loki.write "default" {
            endpoint {
              url = "http://loki:3100/loki/api/v1/push"
            }
          }
          
          discovery.kubernetes "pods" {
            role = "pod"
          }
          
          discovery.relabel "pods" {
            targets = discovery.kubernetes.pods.targets
            rule {
              source_labels = ["__meta_kubernetes_namespace"]
              target_label  = "namespace"
            }
            rule {
              source_labels = ["__meta_kubernetes_pod_name"]
              target_label  = "pod"
            }
            rule {
              source_labels = ["__meta_kubernetes_pod_container_name"]
              target_label  = "container"
            }
          }
          
          loki.source.kubernetes "pod_logs" {
            targets    = discovery.relabel.pods.output
            forward_to = [loki.write.default.receiver]
          }
    EOT
  ]
}