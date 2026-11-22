"""JSON snapshots must have unambiguous fields and finite numeric values.

Unlike an append-only journal, the next snapshot write replaces all old bytes.
Silently choosing a duplicate field would therefore permanently discard data.
"""
import json
import math


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field: ' + key)
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError('nonfinite JSON number: ' + value)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _invalid_number(value)
    return number


def loads(raw):
    return json.loads(raw, object_pairs_hook=_unique_fields,
                      parse_constant=_invalid_number, parse_float=_finite_float)


def dumps(value, **kwargs):
    text = json.dumps(value, allow_nan=False, **kwargs)
    # Python keys such as 1 and '1' can serialize to the same JSON field.
    # Validate the wire representation without normalizing the opaque payload.
    loads(text)
    return text
