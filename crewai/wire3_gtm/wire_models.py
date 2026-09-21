"""Wire models: the same contract, minus the custom validators, for the LLM call.

CrewAI's OpenAI path parses the reply with `chat.completions.parse(response_format=Model)`,
which runs INSIDE the OpenAI SDK. A custom validator that rejects the reply (the Wire3 pricing
rule, the citation rules, the ICP-reference check...) therefore raised out of the LLM call:
no guardrail feedback, no retry, no saved draft, and a 9-minute Analyst step lost (live run
run-20260920-230711).

`wire_model(Model)` rebuilds the model tree with the same fields, types, defaults and Field
constraints (pattern, min/max items, lengths, enums), so the JSON schema sent to OpenAI is
unchanged and constrained decoding still helps, but with no model_validator. The strict model
then validates the reply in the task guardrail (`strict_or_feedback`), where a violation becomes
a readable message for the retry, and the rejected draft is saved like any other.
"""

import types
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError, create_model


def _rewrite(tp, cache: dict):
    """Swap every BaseModel inside a type for its validator-free twin."""
    origin = get_origin(tp)
    if origin is None:
        return wire_model(tp, cache) if isinstance(tp, type) and issubclass(tp, BaseModel) else tp
    args = get_args(tp)
    if origin is Annotated:
        return Annotated[(_rewrite(args[0], cache), *args[1:])]
    if origin in (Union, types.UnionType):
        return Union[tuple(_rewrite(a, cache) for a in args)]
    if origin in (list, set, frozenset):
        return origin[_rewrite(args[0], cache)]
    if origin is dict:
        return dict[args[0], _rewrite(args[1], cache)]
    return tp  # Literal and anything else structural stays as it is


def wire_model(cls: type[BaseModel], cache: dict | None = None) -> type[BaseModel]:
    cache = {} if cache is None else cache
    if cls not in cache:
        fields = {name: (_rewrite(f.annotation, cache), f) for name, f in cls.model_fields.items()}
        cache[cls] = create_model(cls.__name__, __doc__=cls.__doc__, **fields)  # no __base__: no validators come along
    return cache[cls]


def strict_or_feedback(model: type[BaseModel], output, raw: str | None = None) -> tuple[BaseModel | None, str | None]:
    """Validate the reply against the strict model. (object, None) on success; (None, message
    for the retry prompt) on failure. `raw` overrides output.raw (used to validate a repaired copy)."""
    from wire3_gtm.analyst_checks import invalid_reason

    text = output.raw if raw is None else raw
    try:
        return model.model_validate_json(text), None
    except ValidationError:
        return None, invalid_reason(model, text)
    except ValueError:  # not JSON at all
        return None, invalid_reason(model, text)
