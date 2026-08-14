"""Covers the hierarchy request/response contract.

`tests/run_tests.py` at the repo root compares `json.dumps(original, sort_keys=True)`
against the fetched body, so the model has to round-trip every fixture byte for byte.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.node import HierarchyNode, NodeType

FIXTURE_DIR = Path(__file__).resolve().parents[2] / 'tests' / 'objects'
FIXTURE_FILES = sorted(FIXTURE_DIR.glob('*.json'), key=lambda path: int(path.stem))


def leaf_payload():
    return {'id': 142, 'type': 'management_group', 'children': []}


@pytest.mark.parametrize('fixture', FIXTURE_FILES, ids=lambda path: path.name)
def test_fixture_round_trips_byte_identically(fixture):
    original = json.loads(fixture.read_text())
    dumped = HierarchyNode.model_validate(original).model_dump(mode='json')
    assert json.dumps(dumped, sort_keys=True) == json.dumps(original, sort_keys=True)


def test_every_fixture_is_discovered():
    assert [fixture.name for fixture in FIXTURE_FILES] == [
        '1.json', '2.json', '3.json', '4.json', '5.json', '6.json'
    ]


def test_leaf_serialises_children_as_an_empty_list():
    dumped = HierarchyNode.model_validate(leaf_payload()).model_dump(mode='json')
    assert dumped['children'] == []


def test_type_serialises_as_a_bare_string():
    dumped = HierarchyNode.model_validate(leaf_payload()).model_dump(mode='json')
    assert type(dumped['type']) is str
    assert dumped['type'] == 'management_group'


def test_unknown_type_is_rejected():
    payload = leaf_payload() | {'type': 'virtual_machine'}
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate(payload)


def test_extra_key_at_the_root_is_rejected():
    payload = leaf_payload() | {'name': 'prod'}
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate(payload)


def test_extra_key_in_a_nested_child_is_rejected():
    payload = leaf_payload() | {'children': [leaf_payload() | {'id': 7, 'name': 'prod'}]}
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate(payload)


def test_unknown_type_in_a_nested_child_is_rejected():
    payload = leaf_payload() | {'children': [leaf_payload() | {'id': 7, 'type': 'virtual_machine'}]}
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate(payload)


def test_missing_children_is_rejected():
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate({'id': 142, 'type': 'management_group'})


def test_missing_id_is_rejected():
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate({'type': 'management_group', 'children': []})


def test_non_integer_id_is_rejected():
    payload = leaf_payload() | {'id': 'root'}
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate(payload)


def test_node_type_covers_the_three_hierarchy_levels():
    assert {member.value for member in NodeType} == {
        'management_group', 'subscription', 'resource_group'
    }


def test_node_type_members_compare_equal_to_their_bare_string():
    assert NodeType.MANAGEMENT_GROUP == 'management_group'
    assert json.dumps(NodeType.RESOURCE_GROUP) == '"resource_group"'
