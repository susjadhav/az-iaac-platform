"""
Pydantic v2 models for provisioning request payloads.

These are the canonical schemas consumed by both the FastAPI layer
and the Python automation dispatcher. Any breaking change here must
be versioned and coordinated across all repos.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class LifecycleAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    REFRESH = "refresh"
    DELETE = "delete"


class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class ServiceType(str, Enum):
    VIRTUAL_MACHINE = "virtual_machine"
    STORAGE_ACCOUNT = "storage_account"
    KEY_VAULT = "key_vault"
    AKS_CLUSTER = "aks_cluster"
    SQL_DATABASE = "sql_database"
    APP_SERVICE = "app_service"


class AzureRegion(str, Enum):
    UK_SOUTH = "uksouth"
    UK_WEST = "ukwest"
    EAST_US = "eastus"
    EAST_US_2 = "eastus2"
    WEST_US_2 = "westus2"
    WEST_EUROPE = "westeurope"
    NORTH_EUROPE = "northeurope"
    SOUTHEAST_ASIA = "southeastasia"
    AUSTRALIA_EAST = "australiaeast"


# ---------------------------------------------------------------------------
# Service-specific configuration schemas
# ---------------------------------------------------------------------------


class VirtualMachineConfig(BaseModel):
    os_type: str = Field(..., pattern="^(Linux|Windows)$", description="Linux or Windows")
    vm_size: str = Field(..., examples=["Standard_B2ms", "Standard_D4s_v3"])
    os_disk_size_gb: int = Field(default=128, ge=30, le=4096)
    data_disk_size_gb: int | None = Field(default=None, ge=32, le=32767)
    availability_zone: str | None = Field(default=None, pattern="^[1-3]$")
    enable_accelerated_networking: bool = False
    admin_username: str = Field(default="azureuser", min_length=1, max_length=64)
    ssh_public_key: str | None = Field(default=None, description="Required for Linux VMs")

    @model_validator(mode="after")
    def validate_ssh_key_for_linux(self) -> "VirtualMachineConfig":
        if self.os_type == "Linux" and not self.ssh_public_key:
            raise ValueError("ssh_public_key is required for Linux VMs")
        return self


class StorageAccountConfig(BaseModel):
    replication_type: str = Field(
        default="LRS",
        pattern="^(LRS|GRS|RAGRS|ZRS|GZRS|RAGZRS)$",
    )
    access_tier: str = Field(default="Hot", pattern="^(Hot|Cool|Archive)$")
    enable_https_traffic_only: bool = True
    allow_blob_public_access: bool = False
    enable_versioning: bool = True
    soft_delete_retention_days: int = Field(default=7, ge=1, le=365)


class KeyVaultConfig(BaseModel):
    sku: str = Field(default="standard", pattern="^(standard|premium)$")
    enable_purge_protection: bool = True
    soft_delete_retention_days: int = Field(default=90, ge=7, le=90)
    enable_rbac_authorization: bool = True
    enable_disk_encryption: bool = False


class AksClusterConfig(BaseModel):
    node_count: int = Field(default=2, ge=1, le=100)
    min_node_count: int = Field(default=1, ge=1)
    max_node_count: int = Field(default=5, le=100)
    vm_sku: str = Field(default="Standard_D4s_v3")
    kubernetes_version: str | None = Field(default=None, examples=["1.29.0"])
    network_plugin: str = Field(default="azure", pattern="^(azure|kubenet|none)$")
    enable_auto_scaling: bool = True

    @model_validator(mode="after")
    def validate_autoscale_bounds(self) -> "AksClusterConfig":
        if self.min_node_count > self.max_node_count:
            raise ValueError("min_node_count must be <= max_node_count")
        return self


class SqlDatabaseConfig(BaseModel):
    sku_name: str = Field(default="GP_S_Gen5_2", examples=["GP_S_Gen5_2", "BC_Gen5_4"])
    max_size_gb: int = Field(default=32, ge=1, le=4096)
    enable_geo_backup: bool = True
    backup_retention_days: int = Field(default=7, ge=1, le=35)
    zone_redundant: bool = False


class AppServiceConfig(BaseModel):
    runtime_stack: str = Field(..., examples=["PYTHON|3.12", "NODE|20-lts", "DOTNET|8.0"])
    plan_sku: str = Field(default="P1v3", examples=["B1", "P1v3", "P3v3"])
    enable_vnet_integration: bool = False
    always_on: bool = True
    https_only: bool = True


# Union discriminator map — maps ServiceType to its config model
SERVICE_CONFIG_MAP: dict[ServiceType, type[BaseModel]] = {
    ServiceType.VIRTUAL_MACHINE: VirtualMachineConfig,
    ServiceType.STORAGE_ACCOUNT: StorageAccountConfig,
    ServiceType.KEY_VAULT: KeyVaultConfig,
    ServiceType.AKS_CLUSTER: AksClusterConfig,
    ServiceType.SQL_DATABASE: SqlDatabaseConfig,
    ServiceType.APP_SERVICE: AppServiceConfig,
}


# ---------------------------------------------------------------------------
# Top-level request schema
# ---------------------------------------------------------------------------


class ProvisioningRequest(BaseModel):
    """
    The canonical request payload sent from the API layer to the automation
    dispatcher. This is also the schema the GitHub Actions workflow receives
    as a JSON input.
    """

    # Identity & routing
    request_id: str = Field(..., description="UUID generated by the API layer")
    action: LifecycleAction
    service_type: ServiceType

    # Targeting
    organization: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9\-]+$")
    project: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9\-]+$")
    environment: Environment
    region: AzureRegion

    # Requestor
    owner_email: EmailStr
    requestor_name: str = Field(..., min_length=1, max_length=128)

    # Service config — validated by service-specific model downstream
    configuration: dict[str, Any] = Field(
        default_factory=dict,
        description="Service-specific configuration; validated by SERVICE_CONFIG_MAP",
    )

    # Optional: resource name override (platform will auto-generate if omitted)
    resource_name_override: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9\-]{3,63}$",
        description="Override auto-generated name. Must be lowercase alphanumeric + hyphens.",
    )

    # Existing resource reference — required for MODIFY / REFRESH / DELETE
    azure_resource_id: str | None = Field(
        default=None,
        description="Full ARM resource ID. Required for modify, refresh, delete actions.",
    )

    # Pipeline metadata (injected by the GitHub Actions workflow, not the UI)
    pipeline_run_url: str | None = None

    @model_validator(mode="after")
    def validate_resource_id_for_mutations(self) -> "ProvisioningRequest":
        if self.action in (LifecycleAction.MODIFY, LifecycleAction.REFRESH, LifecycleAction.DELETE):
            if not self.azure_resource_id:
                raise ValueError(
                    f"azure_resource_id is required for action '{self.action.value}'"
                )
        return self

    @field_validator("configuration")
    @classmethod
    def validate_configuration_not_empty_for_create(cls, v: dict[str, Any]) -> dict[str, Any]:
        # Detailed per-service validation happens in the dispatcher
        return v

    def validated_service_config(self) -> BaseModel:
        """
        Parse and validate the raw configuration dict into the typed
        service-specific config model. Raises ValidationError on bad input.
        """
        config_model = SERVICE_CONFIG_MAP[self.service_type]
        return config_model(**self.configuration)

    def terraform_workspace_name(self) -> str:
        """Deterministic workspace name: {org}-{project}-{env}"""
        return f"{self.organization}-{self.project}-{self.environment.value}"

    def state_blob_key(self) -> str:
        """Blob key for Terraform state: {org}/{project}/{env}/{service}.tfstate"""
        return f"{self.organization}/{self.project}/{self.environment.value}/{self.service_type.value}.tfstate"

    def resource_name(self) -> str:
        """
        Generate a standardised Azure resource name following the naming convention:
        {org}-{env}-{service_abbrev}-{region_abbrev}
        Falls back to override if provided.
        """
        if self.resource_name_override:
            return self.resource_name_override

        # Short abbreviation map
        service_abbrev = {
            ServiceType.VIRTUAL_MACHINE: "vm",
            ServiceType.STORAGE_ACCOUNT: "st",
            ServiceType.KEY_VAULT: "kv",
            ServiceType.AKS_CLUSTER: "aks",
            ServiceType.SQL_DATABASE: "sql",
            ServiceType.APP_SERVICE: "app",
        }
        region_abbrev = {
            AzureRegion.UK_SOUTH: "uks",
            AzureRegion.UK_WEST: "ukw",
            AzureRegion.EAST_US: "eus",
            AzureRegion.EAST_US_2: "eus2",
            AzureRegion.WEST_US_2: "wus2",
            AzureRegion.WEST_EUROPE: "weu",
            AzureRegion.NORTH_EUROPE: "neu",
            AzureRegion.SOUTHEAST_ASIA: "sea",
            AzureRegion.AUSTRALIA_EAST: "aue",
        }
        svc = service_abbrev[self.service_type]
        reg = region_abbrev[self.region]
        # Truncate org+project to stay within Azure 24-char limits for some resources
        org = self.organization[:8]
        proj = self.project[:8]
        return f"{org}-{proj}-{self.environment.value}-{svc}-{reg}"


# ---------------------------------------------------------------------------
# API-layer response schemas
# ---------------------------------------------------------------------------


class ProvisioningResponse(BaseModel):
    request_id: str
    status: str
    message: str
    pipeline_run_url: str | None = None
    resource_name: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    environment: str
