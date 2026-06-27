# Requirements Document

## Introduction

### Problem Statement

AWS cloud environments accumulate waste and security vulnerabilities silently.
Existing tools (Cloud Custodian, custom scripts) apply rigid rules without
understanding context, dependencies, or risk. Engineers need a system that
reasons about infrastructure the way a senior DevOps engineer would — before
touching anything.

## Glossary

- **ElastiCache**: AWS managed in-memory caching service (Redis or Memcached) used to improve application performance
- **EBS**: Elastic Block Store — AWS block-level storage volumes attached to EC2 instances
- **Security_Groups**: AWS virtual firewalls that control inbound and outbound traffic for resources in a VPC
- **Terraform**: Infrastructure-as-code tool that provisions and manages cloud resources declaratively
- **HCL**: HashiCorp Configuration Language — the declarative language used to write Terraform configuration files
- **Remediation**: The process of applying a fix to resolve an identified waste or security issue in cloud infrastructure
- **Rollback**: The process of reverting a remediation action to restore the previous infrastructure state
- **Audit_Trail**: An append-only log recording every system action with timestamp, actor, and outcome for accountability
- **FinOps**: A practice combining finance and DevOps to optimize cloud spending and eliminate waste
- **SecOps**: A practice combining security and operations to identify and remediate security vulnerabilities
- **Dependency_Check**: An analysis step that identifies resources depending on a target resource before any modification is planned

## Requirements

### User Stories

#### FinOps Discovery

US-01: As a cloud engineer, I want the system to scan my AWS environment and
       identify idle/orphaned resources so I can see what's costing money
       without providing value.
  Acceptance:

- [ ] Finds ElastiCache clusters idle > 30 days
- [ ] Finds unattached EBS volumes > 30 days
- [ ] Reports estimated monthly cost per finding
- [ ] Does not modify any resource during scan

US-02: As a cloud engineer, I want each finding tagged with severity (LOW /
       MEDIUM / HIGH / CRITICAL) so I can prioritise what to fix first.
  Acceptance:

- [ ] All findings have a severity field
- [ ] ElastiCache idle > 30d = HIGH
- [ ] Unattached EBS > 30d = MEDIUM

#### SecOps Discovery

US-03: As a security engineer, I want the system to flag Security Groups with
       0.0.0.0/0 ingress on sensitive ports so I can remediate exposure.
  Acceptance:

- [ ] Detects 0.0.0.0/0 on ports: 22, 3306, 5432, 6379, 27017
- [ ] Reports affected resource ID and port
- [ ] Severity = CRITICAL for database/cache ports

US-04: As a security engineer, I want the system to flag unencrypted storage
       so I can ensure data at rest is protected.
  Acceptance:

- [ ] Checks ElastiCache encryption_at_rest
- [ ] Checks EBS encryption
- [ ] Reports current state vs. required state

#### Remediation Planning

US-05: As a cloud engineer, I want the system to generate a Terraform
       remediation plan AND rollback plan before asking me to approve anything,
       so I can make an informed decision.
  Acceptance:

- [ ] Remediation HCL generated before approval prompt
- [ ] Rollback HCL generated alongside remediation (not after)
- [ ] Both plans shown side-by-side in UI
- [ ] terraform validate passes on both plans

US-06: As a cloud engineer, I want the system to check resource dependencies
       before planning remediation, so I don't break something that depends
       on the resource being removed.
  Acceptance:

- [ ] Dependency check runs before HCL generation
- [ ] If dependency found: block remediation, surface warning
- [ ] If no dependency: proceed to plan

#### Approval & Execution

US-07: As a cloud engineer, I want to explicitly approve each remediation
       action by typing a confirmation string, so nothing runs without my
       intent.
  Acceptance:

- [ ] Approval requires typing "APPROVE <resource-id>"
- [ ] No infrastructure change occurs without approval
- [ ] Approval is logged with timestamp and user

US-08: As a cloud engineer, I want to rollback any remediation within 24h
       by typing a rollback command.
  Acceptance:

- [ ] "ROLLBACK <resource-id>" triggers rollback flow
- [ ] Rollback plan shown before execution
- [ ] Rollback confirmed with "CONFIRM ROLLBACK <resource-id>"
- [ ] Rollback logged to audit trail

#### Observability

US-09: As a cloud engineer, I want every action logged to an audit trail so
       I can prove what the system did and when.
  Acceptance:

- [ ] Audit log written for: scan, plan, approval, execution, rollback
- [ ] Each entry: timestamp, action, resource_id, actor, result
- [ ] Log is append-only (no deletes)

### Out of Scope

- Live AWS credentials / real infrastructure modification
- EC2 rightsizing
- RDS idle detection
- Multi-account support
