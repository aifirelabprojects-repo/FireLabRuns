from typing import Any

_CHILD_RELS = ("phase_info", "company_details", "verification_details", "research_details")

def get_field(obj: Any, name: str) -> Any:
    if hasattr(obj, name):
        return getattr(obj, name)
    for rel in _CHILD_RELS:
        rel_obj = getattr(obj, rel, None)
        if rel_obj is not None and hasattr(rel_obj, name):
            return getattr(rel_obj, name)
    return None

def set_field(obj: Any, name: str, value: Any) -> None:
    for rel in _CHILD_RELS:
        rel_obj = getattr(obj, rel, None)
        if rel_obj is not None and hasattr(rel_obj, name):
            setattr(rel_obj, name, value)
            return
    setattr(obj, name, value)
