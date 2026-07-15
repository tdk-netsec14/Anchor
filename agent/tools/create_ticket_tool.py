"""``create_ticket`` - simulated escalation to a human.

Writes a JSON record to ``TICKETS_DIR``. This is a *simulation* of a ticketing
integration: there is no downstream system, which is stated plainly in the tool
description so the model never tells a user a ticket was "raised with IT".

The summary is written to disk and may be read back, so it is length-capped and
control characters are stripped before it is persisted.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.tools.registry import Tool, ToolError

log = get_logger(__name__)

VALID_PRIORITIES = ("low", "medium", "high", "urgent")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class CreateTicketArgs(BaseModel):
    summary: str = Field(
        min_length=5,
        max_length=500,
        description="One-paragraph description of the user's problem.",
    )
    priority: str = Field(
        default="medium",
        description="One of: low, medium, high, urgent.",
    )

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str) -> str:
        normalised = str(value).strip().lower()
        if normalised not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of: {', '.join(VALID_PRIORITIES)}")
        return normalised

    @field_validator("summary")
    @classmethod
    def _clean_summary(cls, value: str) -> str:
        return _CONTROL_CHARS.sub("", value).strip()


class CreateTicketTool(Tool):
    name = "create_ticket"
    description = (
        "Create a support ticket and escalate the question to a human agent. "
        "Use this only when the knowledge base cannot answer the question, the "
        "user explicitly asks to speak to someone, or the issue is an outage. "
        "This is a demonstration system: no ticket is sent to a real ticketing "
        "system, so do not tell the user it has been routed to IT support."
    )
    args_model = CreateTicketArgs
    # The ticket id is the only handle the user has on the escalation, so it is
    # guaranteed to reach the answer even if the model's prose omits it.
    reference_pattern = r"TCK-[0-9A-F]{8}"
    reference_label = "Ticket reference"

    def __init__(self, tickets_dir: str | Path | None = None) -> None:
        self._tickets_dir = tickets_dir

    @property
    def tickets_dir(self) -> Path:
        return Path(self._tickets_dir or get_settings().TICKETS_DIR)

    def run(self, **kwargs: Any) -> str:
        summary = kwargs["summary"]
        priority = kwargs["priority"]

        ticket = {
            "id": f"TCK-{uuid.uuid4().hex[:8].upper()}",
            "created_at": datetime.now(UTC).isoformat(),
            "priority": priority,
            "summary": summary,
            "status": "open",
            "source": "anchor-agent",
            # Not a real integration - make that obvious to anyone reading the file.
            "simulated": True,
        }

        directory = self.tickets_dir
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{ticket['id']}.json"
            path.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
        except OSError as exc:
            log.error("tool.ticket_write_failed", context={"error_type": type(exc).__name__})
            raise ToolError("The ticket could not be saved right now.") from exc

        log.info(
            "tool.ticket_created",
            context={"ticket_id": ticket["id"], "priority": priority},
        )
        # The id leads, on its own labelled line. A weaker model reliably
        # reports "I have created a ticket" and then drops the reference,
        # which leaves the user with nothing they can quote back to support.
        return (
            f"TICKET ID: {ticket['id']}\n"
            f"Priority: {priority}\n"
            f"Status: created and recorded locally. This demonstration system "
            "does not contact an external ticketing system."
        )
