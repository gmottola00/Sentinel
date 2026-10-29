"""
Claude Vision + tool use analysis of satellite change.

Three tools:
  - get_ndvi_stats:    returns pre-computed NDVI statistics (data injection trick)
  - get_area_metadata: returns area name + bbox
  - classify_change:   structured output trick — Claude calls this to emit its classification
"""

import json
import logging
from typing import Any

import anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)

CHANGE_TYPES = [
    "deforestation",
    "flood",
    "fire",
    "urban_growth",
    "agricultural",
    "other",
    "no_change",
]
SEVERITY_LEVELS = ["low", "medium", "high", "critical"]

TOOLS: list[dict] = [
    {
        "name": "get_ndvi_stats",
        "description": (
            "Returns pre-computed NDVI statistics for the analysed area, "
            "including mean NDVI before and after, mean difference, and the percentage "
            "of pixels that changed significantly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_area_metadata",
        "description": "Returns the name and geographic bounding box of the monitored area.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "classify_change",
        "description": (
            "Call this tool to emit your final classification once you have analysed "
            "the images and statistics. This is the ONLY way to submit your answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "change_type": {
                    "type": "string",
                    "enum": CHANGE_TYPES,
                    "description": "Category of the detected change.",
                },
                "severity": {
                    "type": "string",
                    "enum": SEVERITY_LEVELS,
                    "description": "Severity of the change.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Confidence score between 0 and 1.",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "A concise 2-4 sentence description of the observed change, "
                        "including what changed, where, and why it matters."
                    ),
                },
            },
            "required": ["change_type", "severity", "confidence", "description"],
        },
    },
]

SYSTEM_PROMPT = """You are a satellite imagery analyst specialising in land-cover change detection.
You will be shown two Sentinel-2 true-colour composite images (before and after) and an NDVI difference map.

Your task:
1. Call `get_ndvi_stats` to retrieve the quantitative statistics.
2. Call `get_area_metadata` to understand where the area is.
3. Analyse the images and statistics carefully.
4. Call `classify_change` to submit your final classification.

Focus on:
- Colour changes between before/after (brown/grey patches = loss, new green = growth)
- NDVI diff map: red = vegetation loss, blue = vegetation gain
- Scale of change (% pixels affected)
- Geographic context clues visible in the imagery
"""


class ClaudeAnalyzer:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    def analyze_change(
        self,
        rgb_before_b64: str,
        rgb_after_b64: str,
        diff_image_b64: str,
        ndvi_stats: dict[str, Any],
        area_name: str,
        bbox: dict[str, float],
    ) -> dict[str, Any]:
        """
        Run Claude Vision + tool use loop.
        Returns the structured classification dict + full raw response.
        Blocking (sync Anthropic client) — call via asyncio.to_thread.
        """
        # Pre-build tool result data for injection
        _ndvi_stats_json = json.dumps(ndvi_stats)
        _area_meta_json = json.dumps(
            {
                "name": area_name,
                "bbox": bbox,
                "description": f"Area '{area_name}' bounded by {bbox}",
            }
        )

        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Analyse these Sentinel-2 images for land-cover change detection.\n\n"
                            "Image 1: True-colour composite BEFORE"
                        ),
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": rgb_before_b64,
                        },
                    },
                    {"type": "text", "text": "Image 2: True-colour composite AFTER"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": rgb_after_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Image 3: NDVI difference map "
                            "(blue = vegetation gain, red = vegetation loss, grey = no data)"
                        ),
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": diff_image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Use the available tools to retrieve statistics and metadata, "
                            "then call classify_change with your final answer."
                        ),
                    },
                ],
            }
        ]

        classification: dict | None = None
        raw_responses: list[dict] = []

        # Tool use loop (max 6 turns to avoid runaway)
        for _turn in range(6):
            response = self._client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,  # type: ignore[arg-type]
                messages=messages,
            )

            raw_responses.append(
                {
                    "stop_reason": response.stop_reason,
                    "content": [b.model_dump() for b in response.content],
                }
            )

            # Collect tool calls
            tool_calls = [b for b in response.content if b.type == "tool_use"]
            if not tool_calls:
                # No tool calls → end of loop
                break

            # Build tool results
            tool_results = []
            for tc in tool_calls:
                if tc.name == "classify_change":
                    classification = tc.input  # type: ignore[assignment]
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": "Classification recorded. Thank you.",
                        }
                    )
                elif tc.name == "get_ndvi_stats":
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": _ndvi_stats_json,
                        }
                    )
                elif tc.name == "get_area_metadata":
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": _area_meta_json,
                        }
                    )

            # Append assistant message and tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            if response.stop_reason == "end_turn":
                break

        if classification is None:
            logger.warning("Claude did not call classify_change; using fallback")
            classification = {
                "change_type": "other",
                "severity": "low",
                "confidence": 0.1,
                "description": "Analysis could not be completed automatically.",
            }

        return {
            "classification": classification,
            "raw_claude_response": raw_responses,
        }
