from enum import StrEnum


class NodeType(StrEnum):
    """The three levels of the cloud hierarchy.

    StrEnum, not Enum: members are real strings, so Pydantic serialises `type` as the
    bare value the fixtures use rather than an enum repr, and no caller needs `.value`.
    """

    MANAGEMENT_GROUP = 'management_group'
    SUBSCRIPTION = 'subscription'
    RESOURCE_GROUP = 'resource_group'
