"""Quick validation of reasoning panel logic."""

import json
import tempfile
from pathlib import Path

# Core parsing function (mirrored from app.py)
def parse_reasoning_events(log_path):
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
        except (json.JSONDecodeError, ValueError):
            continue
    return events


# Test 1: Parse actual log file
log_path = Path("agent_reasoning.log")
events = parse_reasoning_events(log_path)
print(f"[OK] Parsed {len(events)} events from agent_reasoning.log")

# Test 2: Malformed lines are skipped
with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
    f.write('{"agent":"a","event_type":"check","resource_id":"r1","message":"ok","timestamp":"2026-01-01T00:00:00Z"}\n')
    f.write("this is not json\n")
    f.write('{"agent":"b","event_type":"finding","resource_id":"r2","message":"found","timestamp":"2026-01-01T00:01:00Z"}\n')
    tmp_path = Path(f.name)

events = parse_reasoning_events(tmp_path)
assert len(events) == 2, f"Expected 2 events, got {len(events)}"
assert events[0]["agent"] == "a"
assert events[1]["agent"] == "b"
print("[OK] Malformed lines skipped silently")

# Test 3: Empty file
with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
    f.write("")
    tmp_path = Path(f.name)

events = parse_reasoning_events(tmp_path)
assert len(events) == 0
print("[OK] Empty file returns empty list")

# Test 4: Non-existent file
events = parse_reasoning_events(Path("nonexistent_file.log"))
assert len(events) == 0
print("[OK] Non-existent file returns empty list")

# Test 5: Section header detection
def build_reasoning_html(events):
    prev_agent = None
    headers = []
    for event in events:
        agent = event.get("agent", "unknown")
        show_header = (agent != prev_agent)
        headers.append(show_header)
        prev_agent = agent
    return headers

test_events = [
    {"agent": "finops", "event_type": "check", "resource_id": "r1", "message": "m1", "timestamp": "t1"},
    {"agent": "finops", "event_type": "finding", "resource_id": "r2", "message": "m2", "timestamp": "t2"},
    {"agent": "secops", "event_type": "check", "resource_id": "r3", "message": "m3", "timestamp": "t3"},
]

headers = build_reasoning_html(test_events)
assert headers == [True, False, True], f"Expected [True, False, True], got {headers}"
print("[OK] Section headers inserted on agent change")

print("\nAll reasoning panel logic tests passed!")
