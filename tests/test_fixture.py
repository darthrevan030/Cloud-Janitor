"""Quick validation of aws_cost_explorer.json fixture."""
import json
from pathlib import Path

fixture_path = Path(__file__).parent / "fixtures" / "aws_cost_explorer.json"
with open(fixture_path) as f:
    data = json.load(f)

resources = data["resources"]
print(f"Total resources: {len(resources)}")

filtered_30 = [r for r in resources if r["idle_days"] >= 30]
print(f"Flaggable (>=30d): {len(filtered_30)}")

not_flagged = [r for r in resources if r["idle_days"] < 30]
print(f"Not flagged (<30d): {len(not_flagged)}")

print("\nAll resources:")
for r in resources:
    print(f"  {r['id']}: type={r['type']}, idle={r['idle_days']}d, cost=${r['monthly_cost']}")

required = {"id", "type", "idle_days", "monthly_cost"}
print("\nSchema checks:")
for r in resources:
    missing = required - set(r.keys())
    status = "OK" if not missing else f"MISSING {missing}"
    print(f"  {r['id']}: {status}")

# Assertions
assert len(resources) == 3, f"Expected 3 resources, got {len(resources)}"
assert len(filtered_30) == 2, f"Expected 2 flaggable, got {len(filtered_30)}"
assert len(not_flagged) == 1, f"Expected 1 not flagged, got {len(not_flagged)}"
print("\nAll assertions passed!")
