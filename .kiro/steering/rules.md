# Infrastructure Remediation Standards

## Terraform Tag Requirements

Every generated resource block must include:
  ManagedBy    = "Kiro-Janitor"
  Environment  = var.environment
  RemediatedAt = timestamp()
  RollbackRef  = "rollbacks/<resource_id>.tf"

## EBS Volume Rules

- Unattached > 7 days: FLAG (severity=MEDIUM)
- Unattached > 30 days: REMEDIATE
  1. aws_ebs_snapshot_copy (snapshot first, depends_on enforced)
  2. aws_ebs_volume destroy
  Rollback: aws_ebs_volume restore from snapshot ARN

## Security Group Rules

- Never delete a Security Group — always narrow CIDR
- Replace 0.0.0.0/0 ingress with data.aws_vpc.current.cidr_block
- Sensitive ports (VPC-only required): 22, 3306, 5432, 6379, 27017
- Rollback: restore original 0.0.0.0/0 rule (stored in rollback HCL)

## ElastiCache Rules

- Idle > 30 days + no active connections: snapshot → delete
- Rollback: restore from snapshot, same node_type and engine version
- New clusters must have: encryption_at_rest=true, auth_token if public subnet

## Approval Gate Protocol

- Display dependency check result first
- Display remediation HCL diff
- Display rollback HCL alongside (not below)
- Require: "APPROVE <resource-id>" — exact match, case-sensitive
- Reject and re-prompt on any other input
- Log approval with: timestamp, resource_id, approver, action

## Error Handling Rules

- Dependency found: surface warning, block remediation, suggest manual review
- terraform validate fails: surface error text, block approval prompt
- Approval string mismatch: display expected format, re-prompt (max 3 attempts)
- Rollback artifact missing: surface error, do not proceed

## Test Quality Standards

Every test written in this project must satisfy the hostile reviewer
standard: "If I deliberately broke the thing this test claims to test,
would this test fail?" If the answer is no, the test is wrong.

### Forbidden patterns — never write these

**TAUTOLOGICAL**: asserting the output of the function equals the output
of the function
  Bad:  assert result == my_function(input)
  Good: assert result == known_expected_value

**PASS-BY-DEFAULT**: assertions that pass on empty or None returns
  Bad:  assert len(result) >= 0
  Good: assert len(result) == 2

**WRONG FIXTURE**: test fixture contains no flaggable data, so the
function could return [] and the test still passes
  Fix: every fixture must contain at least one item that SHOULD be
  flagged, and the test must verify it appears in output

**MISSING NEGATIVE CASES**: no test for what the function should reject
  Every module must have at least one test that verifies the function
  rejects invalid input, returns the wrong thing, or does nothing when
  it should do nothing

**MOCKED AWAY**: mocking the exact unit under test
  Bad:  mock_fn.return_value = expected; assert mock_fn() == expected
  Good: mock only external I/O (file reads, subprocess, network),
  never the function being tested

**NO SCHEMA VALIDATION**: tests that don't verify required fields exist
  Any test on a dict or JSON output must assert required keys exist
  and have the correct types, not just that the call succeeded

**PROPERTY TEST TAUTOLOGY**: Hypothesis test that feeds output back
into the assertion without an independent expected value
  Bad:  result = fn(x); assert result == fn(x)
  Good: assert invariant_that_must_hold_for_any_x(result)

### Required for every new module

When writing tests for a new module, include:
1. At least one test with a known concrete expected value (not derived
   from the function under test)
2. At least one negative test — what the function should NOT do
3. Schema validation for any dict/JSON output
4. If the module writes files: a test that verifies the file was NOT
   modified when it should not have been (check mtime)

### After writing tests

Run pytest. If a test that was previously passing now fails after
you rewrote it to be correct, that is a success — report it as:
"Found lying test: <name> — now correctly fails because <reason>"
Do not revert it to make the suite green. Fix the implementation instead.