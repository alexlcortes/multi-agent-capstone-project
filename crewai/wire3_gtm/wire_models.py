"""Wire models: the same contract, minus the custom validators, for the LLM call.

CrewAI's OpenAI path parses the reply with `chat.completions.parse(response_format=Model)`,
which runs INSIDE the OpenAI SDK. A custom validator that rejects the reply (the Wire3 pricing
rule, the citation rules, the ICP-reference check...) therefore raised out of the LLM call:
no guardrail feedback, no retry, no saved draft, and a 9-minute Analyst step lost (live run
run-20260920-230711).

`wire_model(Model)` rebuilds the model tree with the same fields, types, defaults and Field
constraints (pattern, min/max items, lengths, enums), so the JSON schema sent to OpenAI is
unchanged and constrained decoding still helps, but with no model_validator. Brief-dependent
constraints (competitor names, rows per competitor) are fixed to the active brief when the
wire model is built, so build it after the run's brief is active (tasks.output_models()). The strict model
then validates the reply in the task guardrail (`strict_or_feedback`), where a violation becomes
a readable message for the retry, and the rejected draft is saved like any other.
"""

import copy
import types
from typing import Annotated, Union, get_args, get_origin

from pydantic import (AfterValidator, BaseModel, BeforeValidator, PlainValidator, ValidationError,
                      WrapValidator, create_model)

_FIELD_VALIDATORS = (AfterValidator, BeforeValidator, PlainValidator, WrapValidator)


def _rewrite(tp, cache: dict):
    """Swap every BaseModel inside a type for its validator-free twin."""
    origin = get_origin(tp)
    if origin is None:
        return wire_model(tp, cache) if isinstance(tp, type) and issubclass(tp, BaseModel) else tp
    args = get_args(tp)
    if origin is Annotated:  # field validators stay behind too; schema-only metadata comes along
        keep = _wire_metadata(args[1:])
        inner = _rewrite(args[0], cache)
        return Annotated[(inner, *keep)] if keep else inner
    if origin in (Union, types.UnionType):
        return Union[tuple(_rewrite(a, cache) for a in args)]
    if origin in (list, set, frozenset):
        return origin[_rewrite(args[0], cache)]
    if origin is dict:
        return dict[args[0], _rewrite(args[1], cache)]
    return tp  # Literal and anything else structural stays as it is


def _wire_metadata(metadata) -> list:
    """Drop field validators; swap brief-dependent markers for the active brief's real constraints."""
    out = []
    for m in metadata:
        if isinstance(m, _FIELD_VALIDATORS):
            continue
        out.extend(m.wire_constraints() if hasattr(m, "wire_constraints") else [m])
    return out


def _wire_field(f):
    """Pydantic moves Annotated extras onto FieldInfo.metadata, so they are rewritten there too."""
    f = copy.copy(f)
    f.metadata = _wire_metadata(f.metadata)
    return f


def wire_model(cls: type[BaseModel], cache: dict | None = None) -> type[BaseModel]:
    cache = {} if cache is None else cache
    if cls not in cache:
        fields = {name: (_rewrite(f.annotation, cache), _wire_field(f)) for name, f in cls.model_fields.items()}
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
