from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# `nodes.id` is a Postgres BIGINT, so this is the widest id the store can hold. Bounding
# the schema to the column is what keeps an oversized id a 422 decided here rather than a
# `bigint out of range` from the driver, which would surface as a 500 -- the same reason
# the route types its path parameter as an int instead of taking a string and hoping.
#
# Ids are client-supplied and never generated, so nothing narrower is safe to assume: the
# assignment's fixtures use small positive numbers, but nothing in the contract promises
# that, and rejecting an id the database would have stored is worse than storing it.
BIGINT_MIN = -(2**63)
BIGINT_MAX = 2**63 - 1

NodeId = Annotated[int, Field(ge=BIGINT_MIN, le=BIGINT_MAX)]


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
