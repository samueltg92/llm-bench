import copy
import json
import os
import uuid

from google import genai
from google.genai import types

from .base import StreamEvent, Usage


def sanitize_schema_for_gemini(schema):
    # Keep object property names intact, including names matching schema keywords.
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key in {"$schema", "default", "examples", "additionalProperties", "format"}:
            continue
        if key in {"properties", "$defs", "definitions"}:
            result[key] = {k: sanitize_schema_for_gemini(v) for k, v in value.items()}
        elif key in {"items", "not"}:
            result[key] = sanitize_schema_for_gemini(value)
        elif key in {"anyOf", "oneOf", "allOf"}:
            result[key] = [sanitize_schema_for_gemini(v) for v in value]
        else:
            result[key] = copy.deepcopy(value)
    if "anyOf" in result and len(result["anyOf"]) == 1:
        result.update(result.pop("anyOf")[0])
    return result


class GoogleGenAI:
    def __init__(self, model, timeouts=None):
        self.model = model
        self.client = genai.Client(
            api_key=os.environ[model.api_key_env],
            http_options=types.HttpOptions(
                timeout=int((timeouts or {}).get("read", 120) * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def close(self):
        self.client.close()

    def stream_chat(self, *, messages, tools, temperature, max_output_tokens):
        system = None
        contents = []
        call_names = {}
        for m in messages:
            role = m["role"]
            if role == "system":
                system = m["content"]
                continue
            parts = []
            if role == "tool":
                name = call_names[m["tool_call_id"]]
                result = json.loads(m["content"])
                parts = [
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=name,
                            id=m["tool_call_id"],
                            response=result if isinstance(result, dict) else {"result": result},
                        )
                    )
                ]
            elif m.get("_google_parts"):
                parts = [types.Part.model_validate(p) for p in m["_google_parts"]]
            else:
                if m.get("content"):
                    parts.append(types.Part(text=m["content"]))
                for call in m.get("tool_calls", []):
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                name=call["function"]["name"],
                                id=call["id"],
                                args=json.loads(call["function"]["arguments"]),
                            )
                        )
                    )
            for call in m.get("tool_calls", []):
                call_names[call["id"]] = call["function"]["name"]
            if parts:
                contents.append(
                    types.Content(role="model" if role == "assistant" else "user", parts=parts)
                )
        declarations = [
            types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"]["description"],
                parameters_json_schema=sanitize_schema_for_gemini(t["function"]["parameters"]),
            )
            for t in tools
        ]
        config = {
            "system_instruction": system,
            "max_output_tokens": max_output_tokens,
            "automatic_function_calling": {"disable": True},
            **self.model.extra,
        }
        if self.model.supports_temperature:
            config["temperature"] = temperature
        if declarations:
            config["tools"] = [types.Tool(function_declarations=declarations)]
        index = 0
        for chunk in self.client.models.generate_content_stream(
            model=self.model.model_id,
            contents=contents,
            config=types.GenerateContentConfig(**config),
        ):
            for candidate in chunk.candidates or []:
                for part in (candidate.content.parts if candidate.content else []) or []:
                    native = part.model_dump(mode="json", exclude_none=True)
                    if part.function_call:
                        call = part.function_call
                        cid = call.id or f"call_{uuid.uuid4().hex}"
                        native["function_call"]["id"] = cid
                        yield StreamEvent(
                            kind="tool_call",
                            index=index,
                            call_id=cid,
                            name=call.name,
                            arguments=json.dumps(call.args or {}),
                            native_part=native,
                        )
                        index += 1
                    elif part.text:
                        yield StreamEvent(
                            kind="reasoning" if part.thought else "text",
                            text=part.text,
                            native_part=native,
                        )
                    else:
                        yield StreamEvent(kind="metadata", native_part=native)
                if candidate.finish_reason:
                    yield StreamEvent(kind="done", finish_reason=str(candidate.finish_reason))
            if chunk.usage_metadata:
                u = chunk.usage_metadata
                yield StreamEvent(
                    kind="usage",
                    usage=Usage(
                        prompt_tokens=u.prompt_token_count,
                        completion_tokens=u.candidates_token_count,
                        cached_prompt_tokens=u.cached_content_token_count or 0,
                        reasoning_tokens=u.thoughts_token_count or 0,
                        reasoning_included=False,
                    ),
                )
