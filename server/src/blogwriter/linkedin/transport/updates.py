"""Changing a stored model safely.

``model_copy(update=...)`` skips validation, and a domain model's ``model_dump()`` is camelCase
(``serialize_by_alias``), so merging snake_case keys into a dump would turn the change into an
unknown extra field and silently keep the old value. :func:`revalidated` applies the changes by
field name, then validates the whole document again (model rules included, such as "a post is
publishing exactly while its intent is pending").
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["revalidated"]


def revalidated[M: BaseModel](model: M, **changes: object) -> M:
    """A validated copy of ``model`` with ``changes`` (Python field names)."""
    copy = model.model_copy(update=changes)
    return type(model).model_validate(copy.model_dump(warnings=False))
