"""AISummarizer — generates call summary and action items via Bedrock (Section 6.5).

Trigger: Async Lambda invocation from TranscriptionResultProcessor.
Receives call_id, tenant_id, and transcript_segments.
Calls Bedrock Claude Haiku to produce a summary + action items.
"""

import json
import os
import time

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto import ws_publisher
from sotto.feature_flags import is_enabled
from sotto.logger import logger, metrics, tracer

_bedrock_client = None
_apigw_client = None
WEBSOCKET_API_ENDPOINT = os.environ.get("WEBSOCKET_API_ENDPOINT", "")
BEDROCK_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"

PROMPT_TEMPLATE = """You are an assistant for insurance agency staff. Given this call transcript, provide:
1. A 2-3 sentence summary of what was discussed
2. A list of specific action items (things the agent needs to follow up on)

Be concise. Focus on insurance-relevant details. Do not include pleasantries.

Transcript:
{transcript_text}

Respond in JSON: {{"summary": "...", "action_items": ["...", "..."]}}"""


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime")
    return _bedrock_client


def _get_apigw_client():
    global _apigw_client
    if _apigw_client is None:
        _apigw_client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=WEBSOCKET_API_ENDPOINT,
        )
    return _apigw_client


@logger.inject_lambda_context(log_event=True, correlation_id_path="requestContext.requestId")
@tracer.capture_lambda_handler(capture_response=True)
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, context: LambdaContext) -> dict:
    start_time = time.time()
    logger.debug(
        "Handler entered",
        extra={
            "function_name": context.function_name,
            "function_version": context.function_version,
            "aws_request_id": context.aws_request_id,
            "memory_limit_mb": context.memory_limit_in_mb,
            "remaining_time_ms": context.get_remaining_time_in_millis(),
            "event_keys": list(event.keys()),
        },
    )

    tenant_id = event.get("tenant_id", "")
    call_id = event.get("call_id", "")
    transcript_segments = event.get("transcript_segments", [])

    try:
        _process_summary(tenant_id, call_id, transcript_segments)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return {"statusCode": 200, "body": json.dumps({"status": "processed"})}
    except Exception:
        logger.exception(
            "Unhandled error in AI summarizer",
            extra={"tenant_id": tenant_id, "call_id": call_id},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        raise


@tracer.capture_method
def _process_summary(tenant_id: str, call_id: str, transcript_segments: list[dict]) -> None:
    if not tenant_id or not call_id:
        logger.error("Missing tenant_id or call_id", extra={"tenant_id": tenant_id, "call_id": call_id})
        return

    # Check feature flag
    flag_enabled = is_enabled("ai_summary", tenant_id, db)

    logger.debug(
        "Feature flag check result",
        extra={
            "flag_name": "ai_summary",
            "tenant_id": tenant_id,
            "result": flag_enabled,
        },
    )

    if not flag_enabled:
        logger.debug(
            "AI summary disabled for tenant, setting status to complete",
            extra={"tenant_id": tenant_id, "call_id": call_id},
        )
        db.update_call(tenant_id, call_id, {"status": "complete"})
        _push_summary_event(tenant_id, call_id, summary=None, action_items=None)
        return

    # Build transcript text from segments
    transcript_text = _build_transcript_text(transcript_segments)

    if not transcript_text.strip():
        logger.warning(
            "Empty transcript, skipping Bedrock call",
            extra={"tenant_id": tenant_id, "call_id": call_id},
        )
        db.update_call(tenant_id, call_id, {"status": "complete"})
        return

    # Call Bedrock
    summary, action_items = _invoke_bedrock(tenant_id, call_id, transcript_text)

    # Update call record
    updates = {"status": "complete"}
    if summary is not None:
        updates["summary"] = summary
    if action_items is not None:
        updates["action_items"] = action_items

    logger.debug(
        "Updating call with summary",
        extra={
            "table": "sotto-calls",
            "tenant_id": tenant_id,
            "call_id": call_id,
            "operation": "UpdateItem",
            "has_summary": summary is not None,
            "action_items_count": len(action_items) if action_items else 0,
        },
    )
    db.update_call(tenant_id, call_id, updates)

    _push_summary_event(tenant_id, call_id, summary, action_items)


@tracer.capture_method
def _push_summary_event(
    tenant_id: str, call_id: str, summary: str | None, action_items: list | None,
) -> None:
    if not WEBSOCKET_API_ENDPOINT:
        return

    call = db.get_call(tenant_id, call_id)
    agent_id = call.get("agent_id") if call else None
    if not agent_id:
        return

    event_payload = {
        "event": "summary_ready",
        "call_id": call_id,
        "tenant_id": tenant_id,
    }
    if summary is not None:
        event_payload["summary"] = summary
    if action_items is not None:
        event_payload["action_items"] = action_items

    ws_publisher.push_to_agent(
        agent_id=agent_id,
        tenant_id=tenant_id,
        event=event_payload,
        apigw_client=_get_apigw_client(),
    )


@tracer.capture_method
def _build_transcript_text(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "Unknown")
        text = seg.get("text", "")
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


@tracer.capture_method
def _invoke_bedrock(tenant_id: str, call_id: str, transcript_text: str) -> tuple:
    """Call Bedrock Claude Haiku and return (summary, action_items).

    Returns (None, None) if Bedrock fails — caller still sets status=complete.
    """
    prompt = PROMPT_TEMPLATE.format(transcript_text=transcript_text)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    })

    logger.debug(
        "Invoking Bedrock BEFORE",
        extra={
            "model_id": BEDROCK_MODEL_ID,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "transcript_length": len(transcript_text),
        },
    )

    try:
        invoke_start = time.time()
        response = _get_bedrock_client().invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        invoke_duration_ms = int((time.time() - invoke_start) * 1000)

        logger.debug(
            "Invoking Bedrock AFTER",
            extra={
                "model_id": BEDROCK_MODEL_ID,
                "tenant_id": tenant_id,
                "call_id": call_id,
                "duration_ms": invoke_duration_ms,
                "status": "success",
            },
        )

        response_body = json.loads(response["body"].read())
        return _parse_bedrock_response(response_body, tenant_id, call_id)

    except Exception:
        logger.exception(
            "Bedrock invocation failed — transcript still valuable",
            extra={
                "model_id": BEDROCK_MODEL_ID,
                "tenant_id": tenant_id,
                "call_id": call_id,
            },
        )
        return None, None


@tracer.capture_method
def _parse_bedrock_response(response_body: dict, tenant_id: str, call_id: str) -> tuple:
    """Extract summary and action_items from Bedrock response.

    Returns (summary, action_items) or (None, None) on parse failure.
    """
    try:
        content_blocks = response_body.get("content", [])
        if not content_blocks:
            logger.warning("Empty content from Bedrock", extra={"tenant_id": tenant_id, "call_id": call_id})
            return None, None

        text = content_blocks[0].get("text", "")

        # Try to parse the JSON response
        parsed = json.loads(text)
        summary = parsed.get("summary", "")
        action_items = parsed.get("action_items", [])

        logger.debug(
            "Bedrock response parsed",
            extra={
                "tenant_id": tenant_id,
                "call_id": call_id,
                "summary_length": len(summary),
                "action_items_count": len(action_items),
            },
        )

        return summary, action_items

    except (json.JSONDecodeError, KeyError, IndexError):
        logger.exception(
            "Failed to parse Bedrock response",
            extra={"tenant_id": tenant_id, "call_id": call_id},
        )
        return None, None
