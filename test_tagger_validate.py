"""Quick validation script for ResourceTagger implementation."""
import sys
sys.path.insert(0, '.')
from agents.tagger import ResourceTagger, SAFE_DEFAULT
from unittest.mock import patch, MagicMock
import json

tagger = ResourceTagger()

# Test 1: confidence below threshold nullifies team/owner
result = tagger._validate_single(
    {'env': 'production', 'team': 'backend', 'owner': 'ops', 'risk_level': 'high', 'confidence': 0.5},
    {}
)
assert result['env'] == 'production'
assert result['team'] is None
assert result['owner'] is None
assert result['risk_level'] == 'high'
assert result['confidence'] == 0.5
print('Test 1 PASS: confidence below threshold nullifies team/owner')

# Test 2: confidence at threshold exactly preserves values
result = tagger._validate_single(
    {'env': 'staging', 'team': 'data', 'owner': 'ml-team', 'risk_level': 'medium', 'confidence': 0.7},
    {}
)
assert result['env'] == 'staging'
assert result['team'] == 'data'
assert result['owner'] == 'ml-team'
assert result['confidence'] == 0.7
print('Test 2 PASS: confidence at threshold preserves team/owner')

# Test 3: existing_tags passthrough with non-empty strings
result = tagger._validate_single(
    {'env': 'staging', 'team': 'data', 'owner': 'ml-team', 'risk_level': 'medium', 'confidence': 0.9},
    {'env': 'production', 'team': 'infra', 'owner': 'devops'}
)
assert result['env'] == 'production'
assert result['team'] == 'infra'
assert result['owner'] == 'devops'
print('Test 3 PASS: existing_tags passthrough works')

# Test 4: empty strings and None treated as absent
result = tagger._validate_single(
    {'env': 'staging', 'team': 'data', 'owner': 'ml-team', 'risk_level': 'medium', 'confidence': 0.9},
    {'env': '', 'team': None, 'owner': 'devops'}
)
assert result['env'] == 'staging'
assert result['team'] == 'data'
assert result['owner'] == 'devops'
print('Test 4 PASS: empty strings and None treated as absent')

# Test 5: invalid env defaults to unknown
result = tagger._validate_single(
    {'env': 'invalid', 'team': None, 'owner': None, 'risk_level': 'low', 'confidence': 0.8},
    {}
)
assert result['env'] == 'unknown'
print('Test 5 PASS: invalid env defaults to unknown')

# Test 6: all fields present skips LLM
with patch('agents.tagger.get_client') as mock_client:
    result = tagger.infer('i-123', 'test-instance', {'env': 'production', 'team': 'backend', 'owner': 'ops'})
    mock_client.assert_not_called()
    assert result['env'] == 'production'
    assert result['team'] == 'backend'
    assert result['owner'] == 'ops'
print('Test 6 PASS: all fields present skips LLM call')

# Test 7: exception returns safe default
with patch('agents.tagger.get_client', side_effect=Exception('API down')):
    result = tagger.infer('i-123', 'test-instance', {})
    assert result == SAFE_DEFAULT
print('Test 7 PASS: exception returns safe default')

# Test 8: empty batch returns empty list
result = tagger.infer_batch([])
assert result == []
print('Test 8 PASS: empty batch returns empty list')

# Test 9: batch with mocked LLM
def _make_mock_response(content):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    return mock_resp

batch_response = [
    {'env': 'production', 'team': 'platform', 'owner': 'infra-ops', 'risk_level': 'high', 'confidence': 0.9},
    {'env': 'development', 'team': 'frontend', 'owner': 'ui-team', 'risk_level': 'low', 'confidence': 0.8},
]

with patch('agents.tagger.get_client') as mock_get_client:
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.chat.completions.create.return_value = _make_mock_response(json.dumps(batch_response))

    resources = [
        {'resource_id': 'i-001', 'resource_name': 'prod-api-server', 'existing_tags': {}},
        {'resource_id': 'i-002', 'resource_name': 'dev-test-box', 'existing_tags': {}},
    ]
    results = tagger.infer_batch(resources)
    assert len(results) == 2
    assert results[0]['env'] == 'production'
    assert results[0]['team'] == 'platform'
    assert results[1]['env'] == 'development'
    assert results[1]['team'] == 'frontend'
print('Test 9 PASS: batch inference with mocked LLM works')

# Test 10: batch splits into chunks of 10
with patch('agents.tagger.get_client') as mock_get_client:
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # 12 resources should result in 2 LLM calls (10 + 2)
    chunk1_response = [{'env': 'production', 'team': 'a', 'owner': 'b', 'risk_level': 'low', 'confidence': 0.8}] * 10
    chunk2_response = [{'env': 'staging', 'team': 'c', 'owner': 'd', 'risk_level': 'medium', 'confidence': 0.75}] * 2

    mock_client.chat.completions.create.side_effect = [
        _make_mock_response(json.dumps(chunk1_response)),
        _make_mock_response(json.dumps(chunk2_response)),
    ]

    resources = [{'resource_id': f'i-{i:03d}', 'resource_name': f'server-{i}', 'existing_tags': {}} for i in range(12)]
    results = tagger.infer_batch(resources)
    assert len(results) == 12
    assert mock_client.chat.completions.create.call_count == 2
    # First 10 should be 'production', last 2 should be 'staging'
    assert results[0]['env'] == 'production'
    assert results[10]['env'] == 'staging'
print('Test 10 PASS: batch correctly splits into chunks of 10')

# Test 11: verify module does not import openai directly
import inspect
import agents.tagger
source = inspect.getsource(agents.tagger)
assert 'import openai' not in source
assert 'from openai' not in source
print('Test 11 PASS: no direct openai import')

print('\nAll 11 validation tests passed.')
