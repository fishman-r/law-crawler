"""Small, single-attempt JSON model client; credentials never enter saved config."""
from __future__ import annotations

import json
import math
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ModelClientError(ValueError):
    """Safe error message suitable for a persisted batch report."""


_DEFAULTS = {'timeout': 120, 'num_ctx': 4096, 'max_output_tokens': 1024, 'temperature': 0}
_FIELDS = {'provider', 'base_url', 'model', 'api_key_env',
           'input_price_per_million', 'output_price_per_million', *_DEFAULTS}


def validate_config(config: dict) -> dict:
    """Return a fresh, serializable config, containing only an environment key name."""
    if not isinstance(config, dict) or set(config) - _FIELDS:
        raise ModelClientError('Invalid model configuration fields')
    result = {**_DEFAULTS, **config}
    if result.get('provider') not in ('ollama', 'openai-compatible'):
        raise ModelClientError('Unsupported model provider')
    for field in ('base_url', 'model'):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ModelClientError('Missing model configuration field: ' + field)
        result[field] = result[field].strip()
    try:
        url = urlsplit(result['base_url'])
        valid = (url.scheme in ('http', 'https') and url.hostname and not url.username
                 and not url.password and not url.query and not url.fragment and url.port != 0)
    except ValueError:
        valid = False
    if not valid or any(ord(c) < 33 for c in result['base_url']):
        raise ModelClientError('Invalid model base URL')
    result['base_url'] = result['base_url'].rstrip('/')
    env = result.get('api_key_env')
    if env is not None and (not isinstance(env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env)):
        raise ModelClientError('Invalid API key environment variable name')
    for field in ('timeout', 'num_ctx', 'max_output_tokens', 'temperature',
                  'input_price_per_million', 'output_price_per_million'):
        value = result.get(field)
        if value is None and field.endswith('price_per_million'):
            continue
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
                or (field in ('timeout', 'num_ctx', 'max_output_tokens') and value <= 0)
                or (field in ('num_ctx', 'max_output_tokens') and not isinstance(value, int))):
            raise ModelClientError('Invalid numeric model setting: ' + field)
    return result


def validate_schema(value, schema: dict) -> None:
    """Validate the supported JSON Schema subset, without exposing model text."""
    if not isinstance(schema, dict):
        raise ModelClientError('Invalid JSON schema')
    kind = schema.get('type')
    checks = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
              'string': lambda v: isinstance(v, str), 'boolean': lambda v: isinstance(v, bool),
              'number': lambda v: type(v) in (int, float) and math.isfinite(v),
              'integer': lambda v: type(v) is int, 'null': lambda v: v is None}
    kinds = kind if isinstance(kind, list) else [kind]
    if kind is not None and (any(k not in checks for k in kinds) or not any(checks[k](value) for k in kinds)):
        raise ModelClientError('Model output does not match schema type')
    if 'enum' in schema and not any(type(value) is type(item) and value == item for item in schema['enum']):
        raise ModelClientError('Model output does not match schema enum')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if not set(schema.get('required', [])).issubset(value):
            raise ModelClientError('Model output is missing required fields')
        for key, item in value.items():
            if key in props:
                validate_schema(item, props[key])
            elif schema.get('additionalProperties') is False:
                raise ModelClientError('Model output contains unexpected fields')
            elif isinstance(schema.get('additionalProperties'), dict):
                validate_schema(item, schema['additionalProperties'])
    if isinstance(value, list) and 'items' in schema:
        for item in value:
            validate_schema(item, schema['items'])


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelClientError('Model endpoint redirect rejected')


def _reject_constant(_):
    raise ModelClientError('Model returned invalid JSON')


def _token_count(value):
    return value if type(value) is int and value >= 0 else None


def call_json(config: dict, messages: list, schema: dict) -> dict:
    """Make exactly one request; reject failed, truncated, empty or invalid output."""
    cfg = validate_config(config)
    if not isinstance(messages, list) or not messages or not isinstance(schema, dict):
        raise ModelClientError('Invalid model messages or schema')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if cfg.get('api_key_env'):
        key = os.environ.get(cfg['api_key_env'])
        if not key or '\r' in key or '\n' in key:
            raise ModelClientError('API key environment variable is missing or invalid')
        headers['Authorization'] = 'Bearer ' + key
    payload = {'model': cfg['model'], 'messages': messages, 'stream': False}
    if cfg['provider'] == 'ollama':
        path = '/api/chat'
        payload.update(format=schema, think=False, keep_alive='2m', options={
            'temperature': cfg['temperature'], 'num_ctx': cfg['num_ctx'],
            'num_predict': cfg['max_output_tokens']})
    else:
        path = '/chat/completions'
        payload.update(response_format={'type': 'json_object'}, temperature=cfg['temperature'],
                       max_tokens=cfg['max_output_tokens'])
    start = time.monotonic()
    try:
        request = Request(cfg['base_url'] + path,
                          data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'), headers=headers)
        with build_opener(_NoRedirect()).open(request, timeout=cfg['timeout']) as response:
            if response.status != 200:
                raise ModelClientError('Model endpoint returned unsuccessful HTTP status')
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ModelClientError('Model response exceeds size limit')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdigit()
                                       or int(length) != len(raw)):
                raise ModelClientError('Model HTTP response is incomplete or has invalid length')
        data = json.loads(raw, parse_constant=_reject_constant)
        if not isinstance(data, dict) or data.get('error'):
            raise ModelClientError('Model endpoint returned an error')
        if cfg['provider'] == 'ollama':
            if data.get('done') is not True or data.get('done_reason') not in (None, 'stop'):
                raise ModelClientError('Model output is incomplete or truncated')
            content = data['message']['content']
            input_tokens = _token_count(data.get('prompt_eval_count'))
            output_tokens = _token_count(data.get('eval_count'))
        else:
            choice = data['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ModelClientError('Model output is incomplete or truncated')
            content = choice['message']['content']
            usage = data.get('usage') or {}
            input_tokens = _token_count(usage.get('prompt_tokens'))
            output_tokens = _token_count(usage.get('completion_tokens'))
        if not isinstance(content, str) or not content.strip():
            raise ModelClientError('Model returned empty output')
        output = json.loads(content, parse_constant=_reject_constant)
        validate_schema(output, schema)
    except ModelClientError:
        raise
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise ModelClientError('Model endpoint HTTP error: ' + str(status)) from None
    except TimeoutError:
        raise ModelClientError('Model request timed out') from None
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ModelClientError('Model request timed out') from None
        raise ModelClientError('Model request failed: network transport error') from None
    except json.JSONDecodeError:
        raise ModelClientError('Model response contains invalid JSON') from None
    except UnicodeDecodeError:
        raise ModelClientError('Model response contains invalid text encoding') from None
    except (KeyError, TypeError, IndexError):
        raise ModelClientError('Model response structure is invalid') from None
    except OSError:
        raise ModelClientError('Model request failed: network transport error') from None
    except Exception:
        raise ModelClientError('Model request failed with an unclassified error') from None
    cost = None
    if (input_tokens is not None and output_tokens is not None
            and cfg.get('input_price_per_million') is not None
            and cfg.get('output_price_per_million') is not None):
        cost = (input_tokens * cfg['input_price_per_million'] +
                output_tokens * cfg['output_price_per_million']) / 1_000_000
    return {'output': output, 'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens},
            'model': data.get('model') if isinstance(data.get('model'), str) and data['model'] else cfg['model'],
            'duration_seconds': time.monotonic() - start, 'estimated_cost_usd': cost}
