#!/usr/bin/env bash
# UC-1 Option A: Public ARO HCP Cluster Provisioning
# Source: ARO HCP 2nd Hackathon Guide, Section 4, UC-1
# Jira: RHDPCD-1255
#
# This script documents the EXACT az CLI commands from the hackathon doc,
# plus fixes and additions discovered during live testing (guid jkqh6).
# It is the single source of truth for the Ansible automation.
# Public cluster only — no private cluster/Key Vault steps.
#
# Prerequisites:
#   - Azure CLI 2.67.0+
#   - ARO HCP CLI extension (see Step 0)
#   - jq 1.6+
#   - Service principal with Contributor + User Access Administrator
#   - Resource providers registered: Microsoft.RedHatOpenShift, Microsoft.Compute,
#     Microsoft.Storage, Microsoft.Authorization

set -euo pipefail

###############################################################################
# Environment Variables
###############################################################################
# These are set by the Ansible role from AgnosticD passthrough vars.
# Shown here for documentation.

# LOCATION         - Azure region (must be ARO HCP enabled: eastus2, westeurope, etc.)
# SUBSCRIPTION_ID  - Azure subscription ID
# AZURE_SERVICE_PRINCIPAL_ID - Service principal app ID
# CLUSTER_NAME     - Use RHDP guid for uniqueness
# CUSTOMER_RG_NAME - "${CLUSTER_NAME}-aro-hcp-rg"
# CUSTOMER_NSG     - "${CLUSTER_NAME}-nsg"
# CUSTOMER_VNET_NAME - "${CLUSTER_NAME}-vnet"
# CUSTOMER_VNET_SUBNET1 - "${CLUSTER_NAME}-worker-subnet"
# CUSTOMER_VNET_INTEGRATION_SUBNET_NAME - "${CLUSTER_NAME}-vnet-integration-subnet"
# KV_NAME          - "${CLUSTER_NAME}-kv" (max 24 chars, globally unique)
# MANAGED_RESOURCE_GROUP - "${CLUSTER_NAME}-aro-hcp-managed-rg"
# NP_NAME          - "${CLUSTER_NAME}-np"
# CLUSTER_VERSION  - "4.22"
# NP_VERSION       - "4.22.1"
# ADMIN_PASSWORD   - Password for the Keycloak admin user

###############################################################################
# Step 0: Install ARO HCP CLI extension
###############################################################################
# Official wheel: https://aka.ms/aro-hcp-cli (currently aro_hcp-1.0.0b3)
# The downloaded file must be renamed to match wheel naming convention:
#   curl -sLo aro_hcp-1.0.0b3-py3-none-any.whl https://aka.ms/aro-hcp-cli
#   az extension add --source aro_hcp-1.0.0b3-py3-none-any.whl --yes
# Verify: az aro hcp -h
#
# NOTE: The bennerv/ARO-HCP/releases/0.0.2 wheel (b2) is STALE — it targets
# API version 2026-06-30-preview which is no longer accepted. Always use
# the aka.ms redirect for the current version.

###############################################################################
# Step 1: Create the resource group
###############################################################################
az group create \
  --name "$CUSTOMER_RG_NAME" \
  --subscription "$SUBSCRIPTION_ID" \
  --location "$LOCATION"

###############################################################################
# Step 2: Create the network infrastructure
###############################################################################

# NSG
az network nsg create \
  --name "${CUSTOMER_NSG}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --location "${LOCATION}"

# VNet
az network vnet create \
  --name "${CUSTOMER_VNET_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --location "${LOCATION}" \
  --address-prefixes 10.0.0.0/16

# Worker subnet with NSG
az network vnet subnet create \
  --name "${CUSTOMER_VNET_SUBNET1}" \
  --vnet-name "${CUSTOMER_VNET_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --address-prefixes 10.0.0.0/24 \
  --network-security-group "${CUSTOMER_NSG}" \
  --private-endpoint-network-policies Disabled

# VNet integration subnet with delegation
az network vnet subnet create \
  --name "${CUSTOMER_VNET_INTEGRATION_SUBNET_NAME}" \
  --vnet-name "${CUSTOMER_VNET_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --address-prefixes 10.0.1.0/24 \
  --delegations Microsoft.RedHatOpenShift/hcpOpenShiftClusters

# Capture resource IDs
NSG_ID=$(az network nsg show \
  --name "${CUSTOMER_NSG}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query id --output tsv)

SUBNET_ID=$(az network vnet subnet show \
  --name "${CUSTOMER_VNET_SUBNET1}" \
  --vnet-name "${CUSTOMER_VNET_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query id --output tsv)

VNET_ID=$(az network vnet show \
  --name "${CUSTOMER_VNET_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query id --output tsv)

VNET_INTEGRATION_SUBNET_ID=$(az network vnet subnet show \
  --name "${CUSTOMER_VNET_INTEGRATION_SUBNET_NAME}" \
  --vnet-name "${CUSTOMER_VNET_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query id --output tsv)

###############################################################################
# Step 3: Create the Key Vault and etcd encryption key (PUBLIC)
###############################################################################
az keyvault create \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --name "${KV_NAME}" \
  --enable-rbac-authorization true \
  --public-network-access Enabled \
  --location "${LOCATION}"

KV_ID=$(az keyvault show \
  --name "${KV_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query id -o tsv)

# For service principal (not interactive user), use az ad sp show
SP_OBJECT_ID=$(az ad sp show --id "${AZURE_SERVICE_PRINCIPAL_ID}" --query id -o tsv)

az role assignment create \
  --assignee-object-id "${SP_OBJECT_ID}" \
  --assignee-principal-type ServicePrincipal \
  --role "14b46e9e-c2b7-41b4-b07b-48a6ebf60603" \
  --scope "${KV_ID}"

# RBAC propagation delay — key creation fails with ForbiddenByRbac without this
sleep 30

az keyvault key create \
  --vault-name "${KV_NAME}" \
  --name "etcd-data-kms-encryption-key" \
  --kty RSA \
  --size 2048

ETCD_KEY_VERSION=$(az keyvault key show \
  --vault-name "${KV_NAME}" \
  --name "etcd-data-kms-encryption-key" \
  --query "key.kid" -o tsv | rev | cut -d'/' -f1 | rev)

###############################################################################
# Step 4: Create the managed identities (13 total)
###############################################################################

# 9 Control plane operator identities
az identity create --name "${CLUSTER_NAME}-cp-cluster-api-azure" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-control-plane" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-cloud-controller-manager" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-ingress" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-disk-csi-driver" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-file-csi-driver" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-image-registry" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-cloud-network-config" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-cp-kms" --resource-group "${CUSTOMER_RG_NAME}"

# 3 Data plane operator identities
az identity create --name "${CLUSTER_NAME}-dp-disk-csi-driver" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-dp-file-csi-driver" --resource-group "${CUSTOMER_RG_NAME}"
az identity create --name "${CLUSTER_NAME}-dp-image-registry" --resource-group "${CUSTOMER_RG_NAME}"

# 1 Service managed identity
az identity create --name "${CLUSTER_NAME}-service-managed-identity" --resource-group "${CUSTOMER_RG_NAME}"

# Capture all resource IDs and principal IDs
CLUSTER_API_AZURE_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-cluster-api-azure" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
CLUSTER_API_AZURE_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-cluster-api-azure" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

CONTROL_PLANE_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-control-plane" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
CONTROL_PLANE_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-control-plane" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

CLOUD_CONTROLLER_MANAGER_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-cloud-controller-manager" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
CLOUD_CONTROLLER_MANAGER_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-cloud-controller-manager" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

INGRESS_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-ingress" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
INGRESS_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-ingress" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

DISK_CSI_DRIVER_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-disk-csi-driver" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)

FILE_CSI_DRIVER_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-file-csi-driver" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
FILE_CSI_DRIVER_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-file-csi-driver" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

IMAGE_REGISTRY_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-image-registry" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
IMAGE_REGISTRY_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-image-registry" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

CLOUD_NETWORK_CONFIG_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-cloud-network-config" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
CLOUD_NETWORK_CONFIG_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-cloud-network-config" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

KMS_MI_ID=$(az identity show --name "${CLUSTER_NAME}-cp-kms" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
KMS_MI_PID=$(az identity show --name "${CLUSTER_NAME}-cp-kms" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

DP_DISK_CSI_DRIVER_MI_ID=$(az identity show --name "${CLUSTER_NAME}-dp-disk-csi-driver" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)

DP_FILE_CSI_DRIVER_MI_ID=$(az identity show --name "${CLUSTER_NAME}-dp-file-csi-driver" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
DP_FILE_CSI_DRIVER_MI_PID=$(az identity show --name "${CLUSTER_NAME}-dp-file-csi-driver" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

DP_IMAGE_REGISTRY_MI_ID=$(az identity show --name "${CLUSTER_NAME}-dp-image-registry" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
DP_IMAGE_REGISTRY_MI_PID=$(az identity show --name "${CLUSTER_NAME}-dp-image-registry" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

SERVICE_MI_ID=$(az identity show --name "${CLUSTER_NAME}-service-managed-identity" --resource-group "${CUSTOMER_RG_NAME}" --query id --output tsv)
SERVICE_MI_PID=$(az identity show --name "${CLUSTER_NAME}-service-managed-identity" --resource-group "${CUSTOMER_RG_NAME}" --query principalId --output tsv)

###############################################################################
# Step 5: Create the role assignments
###############################################################################
# Includes 3 data plane assignments NOT in the hackathon doc:
#   - dp-image-registry → IMAGE_REGISTRY_ROLE on VNet
#   - dp-image-registry → IMAGE_REGISTRY_ROLE on Subnet
#   - dp-file-csi-driver → FILE_STORAGE_OPERATOR_ROLE on VNet
# Without these, DataPlaneIdentitiesPermissionsValidation goes Degraded.

# Role definition GUIDs
READER_ROLE="acdd72a7-3385-48ef-bd42-f606fba81ae7"
HCP_CLUSTER_API_PROVIDER_ROLE="88366f10-ed47-4cc0-9fab-c8a06148393e"
HCP_CONTROL_PLANE_OPERATOR_ROLE="fc0c873f-45e9-4d0d-a7d1-585aab30c6ed"
CLOUD_CONTROLLER_MANAGER_ROLE="a1f96423-95ce-4224-ab27-4e3dc72facd4"
INGRESS_OPERATOR_ROLE="0336e1d3-7a87-462b-b6db-342b63f7802c"
FILE_STORAGE_OPERATOR_ROLE="0d7aedc0-15fd-4a67-a412-efad370c947e"
IMAGE_REGISTRY_ROLE="8b32b316-c2f5-4ddf-b05b-83dacd2d08b5"
NETWORK_OPERATOR_ROLE="be7a6435-15ae-4171-8f30-4a343eff9e8f"
FEDERATED_CREDENTIAL_ROLE="ef318e2a-8334-4a05-9e4a-295a196c6a6e"
HCP_SERVICE_MI_ROLE="c0ff367d-66d8-445e-917c-583feb0ef0d4"
KEY_VAULT_CRYPTO_USER_ROLE="12338af0-0e69-4776-bea7-57ae8d297424"

# Control plane operator roles
az role assignment create --assignee-object-id "${CLUSTER_API_AZURE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${HCP_CLUSTER_API_PROVIDER_ROLE}" --scope "${VNET_ID}"

az role assignment create --assignee-object-id "${CONTROL_PLANE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${HCP_CONTROL_PLANE_OPERATOR_ROLE}" --scope "${VNET_ID}"
az role assignment create --assignee-object-id "${CONTROL_PLANE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${HCP_CONTROL_PLANE_OPERATOR_ROLE}" --scope "${NSG_ID}"

az role assignment create --assignee-object-id "${CLOUD_CONTROLLER_MANAGER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${CLOUD_CONTROLLER_MANAGER_ROLE}" --scope "${VNET_ID}"
az role assignment create --assignee-object-id "${CLOUD_CONTROLLER_MANAGER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${CLOUD_CONTROLLER_MANAGER_ROLE}" --scope "${NSG_ID}"

az role assignment create --assignee-object-id "${INGRESS_MI_PID}" --assignee-principal-type ServicePrincipal --role "${INGRESS_OPERATOR_ROLE}" --scope "${VNET_ID}"

az role assignment create --assignee-object-id "${FILE_CSI_DRIVER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FILE_STORAGE_OPERATOR_ROLE}" --scope "${VNET_ID}"
az role assignment create --assignee-object-id "${FILE_CSI_DRIVER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FILE_STORAGE_OPERATOR_ROLE}" --scope "${NSG_ID}"

az role assignment create --assignee-object-id "${IMAGE_REGISTRY_MI_PID}" --assignee-principal-type ServicePrincipal --role "${IMAGE_REGISTRY_ROLE}" --scope "${VNET_ID}"

az role assignment create --assignee-object-id "${CLOUD_NETWORK_CONFIG_MI_PID}" --assignee-principal-type ServicePrincipal --role "${NETWORK_OPERATOR_ROLE}" --scope "${SUBNET_ID}"
az role assignment create --assignee-object-id "${CLOUD_NETWORK_CONFIG_MI_PID}" --assignee-principal-type ServicePrincipal --role "${NETWORK_OPERATOR_ROLE}" --scope "${VNET_ID}"

az role assignment create --assignee-object-id "${KMS_MI_PID}" --assignee-principal-type ServicePrincipal --role "${KEY_VAULT_CRYPTO_USER_ROLE}" --scope "${KV_ID}"

# Service managed identity roles
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${HCP_SERVICE_MI_ROLE}" --scope "${VNET_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${HCP_SERVICE_MI_ROLE}" --scope "${NSG_ID}"

# Service MI → Reader on each control plane identity
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${CLUSTER_API_AZURE_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${CONTROL_PLANE_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${CLOUD_CONTROLLER_MANAGER_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${INGRESS_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${DISK_CSI_DRIVER_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${FILE_CSI_DRIVER_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${IMAGE_REGISTRY_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${CLOUD_NETWORK_CONFIG_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${READER_ROLE}" --scope "${KMS_MI_ID}"

# Data plane operator roles — federated credentials
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FEDERATED_CREDENTIAL_ROLE}" --scope "${DP_DISK_CSI_DRIVER_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FEDERATED_CREDENTIAL_ROLE}" --scope "${DP_FILE_CSI_DRIVER_MI_ID}"
az role assignment create --assignee-object-id "${SERVICE_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FEDERATED_CREDENTIAL_ROLE}" --scope "${DP_IMAGE_REGISTRY_MI_ID}"

az role assignment create --assignee-object-id "${DP_FILE_CSI_DRIVER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FILE_STORAGE_OPERATOR_ROLE}" --scope "${SUBNET_ID}"
az role assignment create --assignee-object-id "${DP_FILE_CSI_DRIVER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FILE_STORAGE_OPERATOR_ROLE}" --scope "${NSG_ID}"

# --- MISSING FROM HACKATHON DOC — discovered during live testing (jkqh6) ---
# dp-file-csi-driver needs FILE_STORAGE_OPERATOR_ROLE on VNet
az role assignment create --assignee-object-id "${DP_FILE_CSI_DRIVER_MI_PID}" --assignee-principal-type ServicePrincipal --role "${FILE_STORAGE_OPERATOR_ROLE}" --scope "${VNET_ID}"

# dp-image-registry needs IMAGE_REGISTRY_ROLE on VNet and Subnet
az role assignment create --assignee-object-id "${DP_IMAGE_REGISTRY_MI_PID}" --assignee-principal-type ServicePrincipal --role "${IMAGE_REGISTRY_ROLE}" --scope "${VNET_ID}"
az role assignment create --assignee-object-id "${DP_IMAGE_REGISTRY_MI_PID}" --assignee-principal-type ServicePrincipal --role "${IMAGE_REGISTRY_ROLE}" --scope "${SUBNET_ID}"

###############################################################################
# Step 6: Create the cluster (Option A — Public)
###############################################################################

USER_ASSIGNED_IDS="{${SERVICE_MI_ID}:{},${CLUSTER_API_AZURE_MI_ID}:{},${CONTROL_PLANE_MI_ID}:{},${CLOUD_CONTROLLER_MANAGER_MI_ID}:{},${INGRESS_MI_ID}:{},${DISK_CSI_DRIVER_MI_ID}:{},${FILE_CSI_DRIVER_MI_ID}:{},${IMAGE_REGISTRY_MI_ID}:{},${CLOUD_NETWORK_CONFIG_MI_ID}:{},${KMS_MI_ID}:{}}"

OPERATOR_AUTH="{user-assigned-identities:{control-plane-operators:{cluster-api-azure:${CLUSTER_API_AZURE_MI_ID},control-plane:${CONTROL_PLANE_MI_ID},cloud-controller-manager:${CLOUD_CONTROLLER_MANAGER_MI_ID},ingress:${INGRESS_MI_ID},disk-csi-driver:${DISK_CSI_DRIVER_MI_ID},file-csi-driver:${FILE_CSI_DRIVER_MI_ID},image-registry:${IMAGE_REGISTRY_MI_ID},cloud-network-config:${CLOUD_NETWORK_CONFIG_MI_ID},kms:${KMS_MI_ID}},data-plane-operators:{disk-csi-driver:${DP_DISK_CSI_DRIVER_MI_ID},file-csi-driver:${DP_FILE_CSI_DRIVER_MI_ID},image-registry:${DP_IMAGE_REGISTRY_MI_ID}},service-managed-identity:${SERVICE_MI_ID}}}"

VAULT_VISIBILITY="Public"

az aro hcp cluster create \
  --name "${CLUSTER_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --location "${LOCATION}" \
  --version "${CLUSTER_VERSION}" \
  --channel-group stable \
  --subnet-id "${SUBNET_ID}" \
  --vnet-integration-subnet-id "${VNET_INTEGRATION_SUBNET_ID}" \
  --nsg "${NSG_ID}" \
  --managed-resource-group-name "${MANAGED_RESOURCE_GROUP}" \
  --key-management-mode CustomerManaged \
  --etcd-encryption-type KMS \
  --kms-vault-name "${KV_NAME}" \
  --vault-visibility "${VAULT_VISIBILITY}" \
  --kms-active-key "{name:etcd-data-kms-encryption-key,version:${ETCD_KEY_VERSION}}" \
  --user-assigned-identities "${USER_ASSIGNED_IDS}" \
  --operators-authentication "${OPERATOR_AUTH}"

# Estimated: ~20 minutes

###############################################################################
# Step 7: Verify the cluster is provisioned
###############################################################################
az aro hcp cluster show \
  --name "${CLUSTER_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query "{provisioningState:properties.provisioningState, version:properties.version.id}" \
  --output json

# Expected: provisioningState: Succeeded, version: 4.22.x

###############################################################################
# Step 8: Create the node pool
###############################################################################
az aro hcp cluster nodepool create \
  --cluster-name "${CLUSTER_NAME}" \
  --name "${NP_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --replicas 2 \
  --vm-size Standard_D4s_v6 \
  --version "${NP_VERSION}" \
  --channel-group stable

# Verify nodepool
az aro hcp cluster nodepool show \
  --cluster-name "${CLUSTER_NAME}" \
  --name "${NP_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --query "properties.provisioningState" \
  --output tsv

# Expected: Succeeded

###############################################################################
# Step 9: Install oc CLI (not pre-installed on bastion)
###############################################################################
OC_VERSION="4.22"
curl -sLo /tmp/openshift-client-linux.tar.gz \
  "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable-${OC_VERSION}/openshift-client-linux.tar.gz"
tar xzf /tmp/openshift-client-linux.tar.gz -C /usr/local/bin oc kubectl
chmod +x /usr/local/bin/oc /usr/local/bin/kubectl

###############################################################################
# Step 10: Request break-glass admin kubeconfig
###############################################################################
# ARO HCP has no kubeadmin. The break-glass credential creates a temporary
# identity (system:customer-break-glass:system-admin) valid for 24 hours.
az aro hcp cluster request-credential \
  --admin \
  --name "${CLUSTER_NAME}" \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --file /tmp/kubeconfig

export KUBECONFIG=/tmp/kubeconfig
oc whoami
# Expected: system:customer-break-glass:system-admin

###############################################################################
# Step 11: Deploy Keycloak as IDP
###############################################################################
# ARO HCP uses "external auth" instead of OCP's built-in OAuth server.
# Entra ID is tenant-specific and won't work in sandbox environments.
# Keycloak runs on the cluster and works with any Azure subscription.
#
# Tested and confirmed: ARO HCP external auth accepts ANY OIDC-compliant
# issuer, not just Entra ID. The issuer-url just needs HTTPS and a valid
# .well-known/openid-configuration endpoint.

KEYCLOAK_NS="keycloak"
KEYCLOAK_ADMIN_USER="admin"
KEYCLOAK_ADMIN_PASS="${ADMIN_PASSWORD}"
KEYCLOAK_REALM="openshift"
CONSOLE_CLIENT_ID="openshift-console"
CONSOLE_CLIENT_SECRET="$(openssl rand -hex 16)"

oc create namespace "${KEYCLOAK_NS}"

# Deploy Keycloak
cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  namespace: ${KEYCLOAK_NS}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: keycloak
  template:
    metadata:
      labels:
        app: keycloak
    spec:
      containers:
      - name: keycloak
        image: quay.io/keycloak/keycloak:24.0
        args: ["start-dev"]
        env:
        - name: KEYCLOAK_ADMIN
          value: "${KEYCLOAK_ADMIN_USER}"
        - name: KEYCLOAK_ADMIN_PASSWORD
          value: "${KEYCLOAK_ADMIN_PASS}"
        - name: KC_PROXY
          value: edge
        - name: KC_HOSTNAME_STRICT
          value: "false"
        - name: KC_HTTP_ENABLED
          value: "true"
        ports:
        - containerPort: 8080
          name: http
        readinessProbe:
          httpGet:
            path: /realms/master
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: "1"
            memory: 1Gi
EOF

cat <<EOF | oc apply -f -
apiVersion: v1
kind: Service
metadata:
  name: keycloak
  namespace: ${KEYCLOAK_NS}
spec:
  selector:
    app: keycloak
  ports:
  - port: 8080
    targetPort: 8080
    name: http
EOF

oc create route edge keycloak --service=keycloak --port=http -n "${KEYCLOAK_NS}"
oc rollout status deployment/keycloak -n "${KEYCLOAK_NS}" --timeout=180s

KEYCLOAK_URL="https://$(oc get route keycloak -n ${KEYCLOAK_NS} -o jsonpath='{.spec.host}')"

# Wait for Keycloak API
for i in $(seq 1 30); do
  curl -sk "${KEYCLOAK_URL}/realms/master" | grep -q realm && break
  sleep 5
done

# Get admin token
ADMIN_TOKEN=$(curl -sk -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=${KEYCLOAK_ADMIN_USER}" \
  -d "password=${KEYCLOAK_ADMIN_PASS}" \
  -d "grant_type=password" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create realm
curl -sk -X POST "${KEYCLOAK_URL}/admin/realms" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"realm\":\"${KEYCLOAK_REALM}\",\"enabled\":true}"

# Create OIDC client for console
CONSOLE_URL="https://console-openshift-console.$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')"
REDIRECT_URI="${CONSOLE_URL}/auth/callback"

curl -sk -X POST "${KEYCLOAK_URL}/admin/realms/${KEYCLOAK_REALM}/clients" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"clientId\": \"${CONSOLE_CLIENT_ID}\",
    \"enabled\": true,
    \"protocol\": \"openid-connect\",
    \"publicClient\": false,
    \"clientAuthenticatorType\": \"client-secret\",
    \"secret\": \"${CONSOLE_CLIENT_SECRET}\",
    \"redirectUris\": [\"${REDIRECT_URI}\"],
    \"webOrigins\": [\"*\"],
    \"standardFlowEnabled\": true,
    \"directAccessGrantsEnabled\": true
  }"

# Create admin user in Keycloak
curl -sk -X POST "${KEYCLOAK_URL}/admin/realms/${KEYCLOAK_REALM}/users" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"admin\",
    \"enabled\": true,
    \"email\": \"admin@example.com\",
    \"firstName\": \"Cluster\",
    \"lastName\": \"Admin\",
    \"emailVerified\": true,
    \"requiredActions\": [],
    \"credentials\": [{\"type\":\"password\",\"value\":\"${KEYCLOAK_ADMIN_PASS}\",\"temporary\":false}]
  }"

###############################################################################
# Step 12: Register Keycloak as external auth on ARO HCP
###############################################################################
ISSUER_URL="${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}"

az aro hcp cluster external-auth create \
  --resource-group "${CUSTOMER_RG_NAME}" \
  --cluster-name "${CLUSTER_NAME}" \
  --name keycloak \
  --issuer-url "${ISSUER_URL}" \
  --issuer-audience "${CONSOLE_CLIENT_ID}" \
  --username-claim preferred_username \
  --username-prefix-policy NoPrefix \
  --clients "[{client-id:${CONSOLE_CLIENT_ID},component:{name:console,auth-client-namespace:openshift-console},type:Confidential}]"

###############################################################################
# Step 13: Create console secrets and grant cluster-admin
###############################################################################
# The console deployment only starts after console-oauth-config secret exists.
# The operator also expects {auth-name}-console-openshift-console.

oc create secret generic console-oauth-config \
  -n openshift-console \
  --from-literal=clientSecret="${CONSOLE_CLIENT_SECRET}"

oc create secret generic keycloak-console-openshift-console \
  -n openshift-console \
  --from-literal=clientSecret="${CONSOLE_CLIENT_SECRET}"

# Grant cluster-admin to the Keycloak admin user
oc adm policy add-cluster-role-to-user cluster-admin admin

# Verify console is up
sleep 30
curl -sk -o /dev/null -w "%{http_code}" "${CONSOLE_URL}"
# Expected: 200

###############################################################################
# Teardown (UC-8) — CRITICAL: delete cluster FIRST, then RG
###############################################################################
# NEVER delete the resource group to tear down the cluster.
# Known bug causes stuck resource group if order is reversed.
#
# 1. Delete external auth
# az aro hcp cluster external-auth delete --cluster-name "${CLUSTER_NAME}" --resource-group "${CUSTOMER_RG_NAME}" --name keycloak --yes
#
# 2. Delete nodepool
# az aro hcp cluster nodepool delete --cluster-name "${CLUSTER_NAME}" --name "${NP_NAME}" --resource-group "${CUSTOMER_RG_NAME}" --yes
#
# 3. Delete cluster (wait for completion)
# az aro hcp cluster delete --name "${CLUSTER_NAME}" --resource-group "${CUSTOMER_RG_NAME}" --yes
#
# 4. Delete resource group
# az group delete --name "${CUSTOMER_RG_NAME}" --yes --no-wait
