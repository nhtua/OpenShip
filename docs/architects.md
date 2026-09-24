# OpenShip Architecture

Detailed architectural decisions and implementation patterns for OpenShip.

---

## Resource Identity Architecture

### The Problem

OpenShip agents create concrete cloud resources from abstract objects. Once provisioned, the agent must be able to:
- Locate the exact resource later for modification or inspection
- Report to engineers what was created
- Handle resource lifecycle operations (update, scale, delete)
- Maintain consistency across multiple execution sessions

### Solution: Dual-State Model

OpenShip uses two complementary state layers:

**1. Abstract State (OpenShip-managed)**
- Stored in Git as part of project versioning
- Maps abstract objects to their intended configuration
- Contains OpenShip identifiers for all objects
- Source of truth for *what should exist*

**2. Concrete State (Platform-managed)**
- Terraform state files for Terraform-managed resources
- OpenShip-managed mapping store for direct API resources
- Contains actual cloud resource identifiers
- Source of truth for *what actually exists*

### Identity Model

**OpenShip Object ID:**
```
openship://{project_id}/{object_type}/{unique_id}
Example: openship://proj-123/deployment/web-service-abc
```

**Resource Mapping Record:**
```json
{
  "openship_id": "openship://proj-123/deployment/web-service-abc",
  "abstract_type": "deployment",
  "concrete_type": "aws_ecs_service",
  "concrete_id": "arn:aws:ecs:us-east-1:123456:service/my-service",
  "platform": "aws",
  "region": "us-east-1",
  "tags": ["openship:proj-123", "openship:web-service-abc"],
  "created_at": "2026-09-16T10:00:00Z",
  "terraform_state_file": "state/aws/main.tfstate",
  "terraform_address": "aws_ecs_service.web_service"
}
```

### State Management by Resource Type

**Terraform-managed resources:**
- Agent generates and maintains Terraform configuration
- Terraform state file is the source of truth for concrete state
- OpenShip maintains abstract state + mapping to Terraform state
- Drift detected via `terraform plan`

**Direct API resources:**
- Agent calls cloud APIs directly (no Terraform)
- OpenShip maintains both abstract and concrete state
- Mappings stored in project's Git repository
- Drift detected by comparing stored state with live API queries

**Hybrid approach:**
- Projects can mix Terraform and direct API resources
- Each resource type uses its appropriate state management strategy
- Agent handles both transparently

### Tagging Convention

All resources created by OpenShip are tagged for discovery:

```
openship:project={project_id}
openship:object={object_id}
openship:workflow={workflow_id}
openship:version={version}
```

**Platform-specific implementations:**
- AWS: Resource tags
- GCP: Resource labels
- Azure: Resource tags
- Kubernetes: Metadata labels
- GitHub Actions: Environment labels

### Execution Context

During workflow execution, the agent maintains a runtime execution context:

```json
{
  "workflow_id": "wf-456",
  "session_id": "sess-789",
  "objects_created": ["openship://proj-123/deployment/web-service-abc"],
  "objects_updated": [],
  "objects_deleted": [],
  "pending_operations": []
}
```

This context:
- Enables immediate follow-up operations ("deploy to the cluster you just created")
- Is persisted to Git at workflow completion
- Is loaded at the start of new sessions for continuity

### Resource Discovery Flow

When the engineer requests an operation on a resource:

1. **Identify target:** Engineer references resource by name, ID, or context ("that deployment")
2. **Resolve abstract ID:** Agent resolves reference to `openship://` ID
3. **Query mapping store:** Agent retrieves mapping record for the abstract ID
4. **Locate concrete resource:** Agent uses the mapping to find the actual cloud resource
5. **Execute operation:** Agent performs the requested operation on the concrete resource
6. **Update state:** Agent updates mapping record and commits to Git

### Drift Detection and Recovery

**Detection:**
- Periodic comparison of concrete state vs. abstract state
- `terraform plan` for Terraform resources
- API queries for direct API resources

**Recovery options:**
- **Refresh:** Update concrete state to match abstract (re-apply)
- **Sync:** Update abstract state to match concrete (adopt current state)
- **Report:** Alert engineer without taking action

### Error Handling

**Mapping not found:**
- Agent searches by tags/labels across platforms
- Agent queries Terraform state for matching resources
- Agent reports to engineer with search results

**Resource deleted externally:**
- Drift detection identifies missing resource
- Agent reports discrepancy
- Engineer decides: recreate, update abstract state, or ignore

**State corruption:**
- Git history enables rollback to previous state
- Agent can reconstruct mapping from Terraform state + tags
- Last resort: manual mapping by engineer

### Security Considerations

- Mapping records are stored in Git (versioned, auditable)
- Concrete IDs may contain sensitive information (ARNs, resource IDs)
- Option to encrypt sensitive fields in mapping records
- Access control follows Git repository permissions
