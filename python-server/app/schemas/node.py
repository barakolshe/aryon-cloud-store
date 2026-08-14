from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# Ids are supplied by the client and land in a BIGINT column, so the contract has to bound
# them. Left unbounded, a Python int is arbitrary precision and sails through validation only
# to fail inside psycopg -- which turns a bad request into a 500. Bounds only: nothing says
# ids are positive, and the column takes negatives happily.
NODE_ID_MIN = -(2**63)
NODE_ID_MAX = 2**63 - 1

NodeId = Annotated[int, Field(ge=NODE_ID_MIN, le=NODE_ID_MAX)]


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

    id: NodeId
    type: NodeType
    children: list['HierarchyNode']


# The self-reference above is a forward reference until the class exists; rebuilding
# resolves it so validation actually recurses into children.
HierarchyNode.model_rebuild()
