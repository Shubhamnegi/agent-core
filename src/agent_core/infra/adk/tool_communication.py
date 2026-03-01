from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from agent_core.infra.adk.tool_runtime_context import get_tool_runtime_context


async def send_slack_message(
    channel: str,
    text: str,
    blocks_json: str | None = None,
    file_path: str | None = None,
    file_name: str | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Send a Slack message to a channel, optionally with blocks and a file.

    Use this when final response content must be delivered to Slack.
    `blocks_json` must be a JSON array string compatible with Slack Block Kit.
    """
    slack_cfg = _resolve_slack_config()
    token = slack_cfg.get("bot_token")
    if not isinstance(token, str) or not token:
        return {
            "status": "not_configured",
            "reason": "slack_token_missing",
            "channel": channel,
        }

    parsed_blocks: list[dict[str, Any]] | None = None
    if blocks_json:
        try:
            parsed_blocks_raw = json.loads(blocks_json)
        except json.JSONDecodeError:
            return {
                "status": "failed",
                "reason": "invalid_blocks_json",
                "channel": channel,
            }
        if not isinstance(parsed_blocks_raw, list):
            return {
                "status": "failed",
                "reason": "blocks_json_must_be_array",
                "channel": channel,
            }
        parsed_blocks = [item for item in parsed_blocks_raw if isinstance(item, dict)]

    def _send() -> dict[str, Any]:
        client = _build_slack_client(token=token, base_url=_to_optional_str(slack_cfg.get("base_url")))
        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "text": text,
            }
            if parsed_blocks is not None:
                kwargs["blocks"] = parsed_blocks
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            posted = client.chat_postMessage(**kwargs)
            message_ts = _to_optional_str(posted.get("ts"))

            upload_result: dict[str, Any] | None = None
            if file_path:
                path = Path(file_path)
                if not path.exists() or not path.is_file():
                    return {
                        "status": "failed",
                        "reason": "file_not_found",
                        "channel": channel,
                        "path": file_path,
                    }

                filename = file_name or path.name
                upload = client.files_upload_v2(
                    channel=channel,
                    file=str(path),
                    filename=filename,
                    title=filename,
                    thread_ts=thread_ts or message_ts,
                )
                upload_result = {
                    "status": "ok",
                    "file": upload.get("file"),
                    "files": upload.get("files"),
                }

            return {
                "status": "ok",
                "channel": channel,
                "message_ts": message_ts,
                "message": {
                    "ts": message_ts,
                    "channel": _to_optional_str(posted.get("channel")),
                    "text": _to_optional_str(posted.get("message", {}).get("text")) or text,
                },
                "file_upload": upload_result,
            }
        except SlackApiError as exc:
            return {
                "status": "failed",
                "reason": "slack_api_error",
                "channel": channel,
                "error": str(exc),
                "slack_error": _to_optional_str(getattr(exc.response, "get", lambda *_: None)("error")),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "reason": f"slack_request_failed:{exc}",
                "channel": channel,
            }

    return await asyncio.to_thread(_send)


async def read_slack_messages(
    channel: str,
    limit: int = 20,
    include_files: bool = True,
) -> dict[str, Any]:
    """Read recent Slack messages from a channel.

    Use this when the workflow needs communication context from Slack.
    Returns normalized message entries with optional attached-file metadata.
    """
    slack_cfg = _resolve_slack_config()
    token = slack_cfg.get("bot_token")
    if not isinstance(token, str) or not token:
        return {
            "status": "not_configured",
            "reason": "slack_token_missing",
            "channel": channel,
        }

    safe_limit = max(1, min(limit, 200))

    def _read() -> dict[str, Any]:
        client = _build_slack_client(token=token, base_url=_to_optional_str(slack_cfg.get("base_url")))
        try:
            response = client.conversations_history(channel=channel, limit=safe_limit)
        except SlackApiError as exc:
            return {
                "status": "failed",
                "reason": "slack_api_error",
                "channel": channel,
                "error": str(exc),
                "slack_error": _to_optional_str(getattr(exc.response, "get", lambda *_: None)("error")),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "reason": f"slack_request_failed:{exc}",
                "channel": channel,
            }

        messages_raw = response.get("messages", [])
        normalized: list[dict[str, Any]] = []
        for raw in messages_raw:
            if not isinstance(raw, dict):
                continue
            item: dict[str, Any] = {
                "ts": _to_optional_str(raw.get("ts")),
                "thread_ts": _to_optional_str(raw.get("thread_ts")),
                "user": _to_optional_str(raw.get("user")),
                "text": _to_optional_str(raw.get("text")) or "",
            }
            if include_files:
                item["files"] = _normalize_slack_file_entries(raw.get("files"))
            normalized.append(item)

        return {
            "status": "ok",
            "channel": channel,
            "count": len(normalized),
            "messages": normalized,
        }

    return await asyncio.to_thread(_read)


async def send_email_smtp(
    to_emails: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    cc_emails: str | None = None,
    bcc_emails: str | None = None,
    attachment_paths_json: str | None = None,
) -> dict[str, Any]:
    """Send email through configured SMTP settings.

    Use this for outbound email delivery with plain text/HTML and optional
    file attachments.
    """
    smtp_cfg = _resolve_smtp_config()
    host = _to_optional_str(smtp_cfg.get("host"))
    port = smtp_cfg.get("port")
    from_email = _to_optional_str(smtp_cfg.get("from_email"))

    if not host or not isinstance(port, int) or not from_email:
        return {
            "status": "not_configured",
            "reason": "smtp_config_incomplete",
        }

    to_list = _parse_csv_emails(to_emails)
    cc_list = _parse_csv_emails(cc_emails)
    bcc_list = _parse_csv_emails(bcc_emails)
    recipients = to_list + cc_list + bcc_list
    if not recipients:
        return {
            "status": "failed",
            "reason": "no_recipients",
        }

    attachment_paths = _parse_string_list_json(attachment_paths_json)
    if attachment_paths_json is not None and attachment_paths is None:
        return {
            "status": "failed",
            "reason": "invalid_attachment_paths_json",
        }

    username = _to_optional_str(smtp_cfg.get("username"))
    password = _to_optional_str(smtp_cfg.get("password"))
    use_tls = bool(smtp_cfg.get("use_tls", True))
    use_ssl = bool(smtp_cfg.get("use_ssl", False))
    from_name = _to_optional_str(smtp_cfg.get("from_name"))

    def _send_mail() -> dict[str, Any]:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((from_name, from_email)) if from_name else from_email
        message["To"] = ", ".join(to_list)
        if cc_list:
            message["Cc"] = ", ".join(cc_list)
        if body_text:
            message.set_content(body_text)
        else:
            message.set_content("See HTML body.")
        if body_html:
            message.add_alternative(body_html, subtype="html")

        attachment_count = 0
        for raw_path in attachment_paths or []:
            path = Path(raw_path)
            if not path.exists() or not path.is_file():
                return {
                    "status": "failed",
                    "reason": "attachment_not_found",
                    "path": raw_path,
                }
            mime_type, _ = mimetypes.guess_type(path.name)
            main_type, sub_type = (
                mime_type.split("/", 1)
                if mime_type and "/" in mime_type
                else ("application", "octet-stream")
            )
            message.add_attachment(
                path.read_bytes(),
                maintype=main_type,
                subtype=sub_type,
                filename=path.name,
            )
            attachment_count += 1

        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(
                    host,
                    port,
                    timeout=20,
                    context=ssl.create_default_context(),
                )
            else:
                server = smtplib.SMTP(host, port, timeout=20)

            with server:
                if use_tls and not use_ssl:
                    server.starttls(context=ssl.create_default_context())
                if username:
                    server.login(username, password or "")
                server.send_message(message, to_addrs=recipients)
        except Exception as exc:
            return {
                "status": "failed",
                "reason": f"smtp_send_failed:{exc}",
            }

        return {
            "status": "ok",
            "subject": subject,
            "recipient_count": len(recipients),
            "attachment_count": attachment_count,
        }

    return await asyncio.to_thread(_send_mail)


def _build_slack_client(token: str, base_url: str | None) -> WebClient:
    if base_url:
        return WebClient(token=token, base_url=base_url)
    return WebClient(token=token)


def _normalize_slack_file_entries(raw_files: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_files, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "id": _to_optional_str(item.get("id")),
                "name": _to_optional_str(item.get("name")),
                "title": _to_optional_str(item.get("title")),
                "filetype": _to_optional_str(item.get("filetype")),
                "mimetype": _to_optional_str(item.get("mimetype")),
                "size": item.get("size"),
            }
        )
    return normalized


def _resolve_slack_config() -> dict[str, Any]:
    config = _load_communication_config()
    raw = config.get("slack") if isinstance(config, dict) else None
    slack = raw if isinstance(raw, dict) else {}
    token = _resolve_secret(
        explicit=_to_optional_str(slack.get("bot_token")),
        env_name=_to_optional_str(slack.get("bot_token_env")) or "SLACK_BOT_TOKEN",
    )
    return {
        "bot_token": token,
        "base_url": _to_optional_str(slack.get("base_url")) or "https://slack.com/api",
    }


def _resolve_smtp_config() -> dict[str, Any]:
    config = _load_communication_config()
    raw = config.get("smtp") if isinstance(config, dict) else None
    smtp = raw if isinstance(raw, dict) else {}
    password = _resolve_secret(
        explicit=_to_optional_str(smtp.get("password")),
        env_name=_to_optional_str(smtp.get("password_env")) or "SMTP_PASSWORD",
    )
    return {
        "host": _to_optional_str(smtp.get("host")),
        "port": smtp.get("port"),
        "username": _to_optional_str(smtp.get("username")),
        "password": password,
        "use_tls": bool(smtp.get("use_tls", True)),
        "use_ssl": bool(smtp.get("use_ssl", False)),
        "from_email": _to_optional_str(smtp.get("from_email")),
        "from_name": _to_optional_str(smtp.get("from_name")),
    }


def _load_communication_config() -> dict[str, Any]:
    context = get_tool_runtime_context()
    configured_path = context.communication_config_path if context is not None else None
    path = Path(configured_path or "config/communication_config.json")
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_secret(explicit: str | None, env_name: str) -> str | None:
    if explicit:
        return explicit
    env_value = os.getenv(env_name)
    return env_value.strip() if isinstance(env_value, str) and env_value.strip() else None


def _parse_csv_emails(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_string_list_json(raw: str | None) -> list[str] | None:
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    output: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            return None
        output.append(item.strip())
    return output


def _to_optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
