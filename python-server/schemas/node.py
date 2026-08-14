from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class NodeType(StrEnum):
    """The three levels of the cloud hierarchy.

    StrEnum, not Enum: members are real strings, so Pydantic serialises `type` as the
    bare value the fixtures use rather than an enum repr, and no caller needs `.value`.
    """

    MANAGEMENT_GROUP = 'management_group'
    SUBSCRIPTION = 'subscription'
    RESOURCE_GROUP = 'resource_group'


class HierarchyNode(BaseModel):
    """A node and everything nested underneath it -- the body of both hierarchy endpoints.

    `tests/run_tests.py` compares `json.dumps(original, sort_keys=True)` against the
    fetched body, so the serialised shape has to be exactly {id, type, children}:
    `extra='forbid'` keeps unknown keys out on the way in, and `children` is required
    rather than defaulted so a leaf always carries an explicit empty list.
    """

    model_config = ConfigDict(extra='forbid')

    id: int
    type: NodeType
    children: list['HierarchyNode']


# The self-reference above is a forward reference until the class exists; rebuilding
# resolves it so validation actually recurses into children.
HierarchyNode.model_rebuild()
