import json

from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser


class BoundedJSONParser(BaseParser):
    media_type = "application/json"

    def parse(self, stream, media_type=None, parser_context=None):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result

        def invalid_constant(_):
            raise ValueError("Non-finite JSON number")

        raw = stream.read(settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1)
        if len(raw) > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            raise ParseError("Request exceeds 10 MiB.")
        try:
            value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
            pending = [(value, 0)]
            nodes = 0
            while pending:
                item, depth = pending.pop()
                nodes += 1
                if depth > 40 or nodes > 250000:
                    raise ValueError("JSON complexity limit")
                children = (
                    item.values()
                    if isinstance(item, dict)
                    else item
                    if isinstance(item, list)
                    else []
                )
                pending.extend((child, depth + 1) for child in children)
            return value
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ParseError("Invalid or excessively complex JSON.") from exc
