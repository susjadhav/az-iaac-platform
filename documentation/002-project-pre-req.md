# 002 — Project prerequisites

**Infrastructure Automation Platform · Azure Resource Guide**

This document covers everything you need to provision in Azure before the platform can run, in the exact order you should create it.

---

## Overview — resource groups by purpose

```
┌─────────────────────────────────────────────────────────────────────┐
│  1 — Identity                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────┐ │
│  │ Service Principal│  │ RBAC assignments │  │Federated credenti.│ │
│  │ App reg + secret │  │ Contributor on   │  │OIDC for GitHub    │ │
│  │                  │  │ target RGs       │  │Actions            │ │
│  └──────────────────┘  └──────────────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  2 — Terraform state backend                                        │
│  One dedicated resource group, locked against deletion              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────┐ │
│  │ Resource group   │  │ Storage account  │  │ Blob container    │ │
│  │rg-{org}-{env}-tf │  │ GRS · versioning │  │terraform-state    │ │
│  │                  │  │ · soft-delete    │  │· private          │ │
│  └──────────────────┘  └──────────────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  3 — Inventory database                                             │
│  Tracks every provisioned resource — the live source of truth       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────┐ │
│  │ Cosmos DB account│  │ Database         │  │ Container         │ │
│  │ Serverless       │  │ iaac-platform    │  │resource-inventory │ │
│  │ SQL API          │  │                  │  │partition: /project│ │
│  └──────────────────┘  └──────────────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  4 — Logging & observability                                        │
│  Execution logs, Terraform plan output, API traces                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────┐ │
│  │ Storage account  │  │ Log Analytics    │  │ App Insights      │ │
│  │ logs container   │  │ workspace        │  │ FastAPI telemetry │ │
│  │ 90d retention    │  │ 30d retention    │  │ (Phase 4)         │ │
│  └──────────────────┘  └──────────────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  5 — Target resources  (provisioned on demand by users)             │
│  Each gets its own resource group · created by Terraform via the SP │
│  ┌──────┐  ┌──────────┐  ┌──────────┐  ┌──────┐  ┌─────────────┐ │
│  │ VMs  │  │ Storage  │  │Key Vault │  │ AKS  │  │SQL / App Svc│ │
│  └──────┘  └──────────┘  └──────────┘  └──────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

> Create groups 1–4 once. Group 5 is created automatically by the platform on user request.
>
> **Minimum for Phase 1:** groups 1, 2, and 3 only.

---

## Minimum for Phase 1

| Resource | Required now | Why |
|---|---|---|
| Service Principal | Yes | Terraform cannot authenticate without it |
| State storage account | Yes | `terraform init` fails without a backend |
| Cosmos DB | Yes | Dispatcher writes inventory records immediately |
| Log storage | No | Logs simply will not upload — everything else works |
| Log Analytics / App Insights | No | Phase 4 |

---

## Group 1 — Service Principal (create this first)

Everything authenticates through one service principal. Create it and note the output — you need the values for every subsequent step.

```bash
# Create the SP and assign Contributor at subscription scope
az ad sp create-for-rbac \
  --name "sp-iaac-platform-dev" \
  --role Contributor \
  --scopes /subscriptions/<YOUR_SUBSCRIPTION_ID> \
  --sdk-auth
```

The output gives you four values — save them all:

| Output field | Used as |
|---|---|
| `clientId` | `ARM_CLIENT_ID` / `AZURE_CLIENT_ID` |
| `clientSecret` | `ARM_CLIENT_SECRET` — store in GitHub Secrets, never in code |
| `tenantId` | `ARM_TENANT_ID` |
| `subscriptionId` | `ARM_SUBSCRIPTION_ID` |

### OIDC federated credential (recommended for CI — no long-lived secret)

```bash
az ad app federated-credential create \
  --id <APP_ID> \
  --parameters '{
    "name": "github-actions-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:your-org/infra-automation-core:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenCredential"]
  }'
```

With OIDC configured, GitHub Actions authenticates using a short-lived token rather than a stored secret. The `ARM_CLIENT_SECRET` environment variable is not needed in CI.

---

## Group 2 — Terraform state backend

This is automated — run the bootstrap Terraform module we already wrote in `infra-terraform-modules/backend/`.

### Option A — Terraform (recommended)

```bash
cd infra-terraform-modules/backend

terraform init

terraform apply \
  -var="environment=dev" \
  -var="organization=acme" \
  -var="location=uksouth" \
  -var="automation_service_principal_object_id=<SP_OBJECT_ID>"

# Note the storage account name for your .env
terraform output storage_account_name
```

### Option B — Azure CLI (manual)

```bash
# Resource group
az group create \
  --name "rg-acme-dev-tfstate" \
  --location uksouth

# Storage account
az storage account create \
  --name "tfstateacmedev" \
  --resource-group "rg-acme-dev-tfstate" \
  --sku Standard_LRS \
  --allow-blob-public-access false \
  --min-tls-version TLS1_2

# State container
az storage container create \
  --name "terraform-state" \
  --account-name "tfstateacmedev" \
  --auth-mode login

# Delete lock — prevents accidental destruction
az lock create \
  --name "lock-tfstate" \
  --resource-group "rg-acme-dev-tfstate" \
  --lock-type CanNotDelete
```

### State backend naming convention

Blob keys follow this pattern — one state file per service per workspace:

```
{organization}/{project}/{environment}/{service}.tfstate

# Example
acme/webapp/prod/virtual_machine.tfstate
acme/webapp/prod/key_vault.tfstate
acme/payments/dev/storage_account.tfstate
```

---

## Group 3 — Cosmos DB inventory

```bash
# Create account — serverless capacity mode (cheapest for dev, scales automatically)
az cosmosdb create \
  --name "cosmos-iaac-dev" \
  --resource-group "rg-acme-dev-platform" \
  --capabilities EnableServerless \
  --default-consistency-level Session \
  --locations regionName=uksouth

# Create database
az cosmosdb sql database create \
  --account-name "cosmos-iaac-dev" \
  --resource-group "rg-acme-dev-platform" \
  --name "iaac-platform"

# Create container with partition key /project
az cosmosdb sql container create \
  --account-name "cosmos-iaac-dev" \
  --resource-group "rg-acme-dev-platform" \
  --database-name "iaac-platform" \
  --name "resource-inventory" \
  --partition-key-path "/project"

# Get the endpoint URL for your .env
az cosmosdb show \
  --name "cosmos-iaac-dev" \
  --resource-group "rg-acme-dev-platform" \
  --query documentEndpoint \
  --output tsv

# Grant the SP data access
az cosmosdb sql role assignment create \
  --account-name "cosmos-iaac-dev" \
  --resource-group "rg-acme-dev-platform" \
  --role-definition-name "Cosmos DB Built-in Data Contributor" \
  --principal-id <SP_OBJECT_ID> \
  --scope "/"
```

### Container design decisions

| Decision | Value | Reason |
|---|---|---|
| Partition key | `/project` | Most queries are scoped to a project |
| Capacity mode | Serverless | No pre-provisioned RUs; cost scales with actual usage |
| Consistency | Session | Reads always see writes from the same session — correct for the dispatcher pattern |
| Secondary indexes | `organization`, `environment`, `resource_type`, `status`, `owner_email` | Enables cross-project governance queries without cross-partition scans |

---

## Group 4 — Logging & observability

```bash
# Storage account for execution logs and Terraform plan output
az storage account create \
  --name "stiaacdevlogs" \
  --resource-group "rg-acme-dev-platform" \
  --sku Standard_LRS \
  --allow-blob-public-access false

# Logs container
az storage container create \
  --name "logs" \
  --account-name "stiaacdevlogs" \
  --auth-mode login

# Grant SP write access
az role assignment create \
  --role "Storage Blob Data Contributor" \
  --assignee <SP_CLIENT_ID> \
  --scope "$(az storage account show \
      --name stiaacdevlogs \
      --resource-group rg-acme-dev-platform \
      --query id --output tsv)"
```

### Log retention policy

| Log type | Storage location | Retention | Access |
|---|---|---|---|
| Execution logs | `logs/{request_id}/execution.log` | 90 days | Platform team |
| Terraform plan output | `logs/{request_id}/plan.json` | 90 days | Platform team |
| API access logs | Azure Monitor / App Insights | 30 days | Platform team |
| Audit trail | Azure SQL: `request_audit` table | 7 years | Compliance |

---

## Environment variables — final `.env`

After completing groups 1–4, populate `infra-automation-core/config/.env`:

```bash
# Group 1 — Identity
ARM_CLIENT_ID=<clientId from SP output>
ARM_TENANT_ID=<tenantId from SP output>
ARM_SUBSCRIPTION_ID=<your subscription ID>
ARM_CLIENT_SECRET=<clientSecret — local dev only; use OIDC in CI>

# Group 2 — State backend
# (consumed automatically by Terraform backend config — no env var needed)

# Group 3 — Cosmos DB inventory
COSMOS_ENDPOINT=https://cosmos-iaac-dev.documents.azure.com:443/
COSMOS_DATABASE=iaac-platform

# Group 4 — Logging
AZURE_LOG_STORAGE_ACCOUNT=stiaacdevlogs
AZURE_LOG_CONTAINER=logs

# Email
EMAIL_BACKEND=sendgrid
SENDGRID_API_KEY=SG.xxxxxxxxxxxxxxxxxx
EMAIL_SENDER=noreply@iaac-platform.io

# Pipeline
PIPELINE_BACKEND=github
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxx
GITHUB_REPO_OWNER=your-org
GITHUB_REPO_NAME=infra-automation-core
GITHUB_WORKFLOW_ID=provision.yml
GITHUB_WORKFLOW_REF=main

# Terraform
TF_MODULES_ROOT=/path/to/infra-terraform-modules
```

---

## GitHub Actions secrets

These must be added to the repository under **Settings → Secrets and variables → Actions**:

| Secret name | Value | Notes |
|---|---|---|
| `AZURE_CLIENT_ID` | SP client ID | Used for OIDC login |
| `AZURE_TENANT_ID` | Azure AD tenant ID | Used for OIDC login |
| `AZURE_SUBSCRIPTION_ID` | Target subscription ID | Used for OIDC login |
| `COSMOS_ENDPOINT` | Cosmos DB endpoint URL | Inventory client |
| `AZURE_LOG_STORAGE_ACCOUNT` | Storage account name | Log uploader |
| `SENDGRID_API_KEY` | SendGrid API key | Email notifications |

> `ARM_CLIENT_SECRET` is **not** added as a GitHub secret when using OIDC. The federated credential in Group 1 replaces it.

---

## Checklist — ready to run

```
[ ] az login succeeds and correct subscription is selected
[ ] Service principal created, client ID and tenant ID noted
[ ] Federated credential added (for GitHub Actions OIDC)
[ ] State storage account created and container verified
[ ] Cosmos DB account, database, and container created
[ ] SP granted Cosmos DB Built-in Data Contributor role
[ ] Log storage account created (optional for Phase 1)
[ ] .env file populated with all values above
[ ] GitHub Actions secrets added to the repository
[ ] terraform init succeeds in infra-terraform-modules/backend/
[ ] PYTHONPATH=. pytest tests/ -v passes (26 tests, no Azure needed)
```

---

*Document: 002 — Project prerequisites · Infrastructure Automation Platform v1.1*