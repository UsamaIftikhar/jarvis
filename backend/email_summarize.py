"""Build user-facing email replies from Gmail tool output only — no LLM guessing."""
from __future__ import annotations

import re
from typing import Any

_GMAIL_TOOL_RE = re.compile(r"(gmail__\w+) → (.+)$", re.DOTALL)
_HEADER_RE = {
    "subject": re.compile(r"^Subject:\s*(.+)$", re.MULTILINE),
    "from": re.compile(r"^From:\s*(.+)$", re.MULTILINE),
    "date": re.compile(r"^Date:\s*(.+)$", re.MULTILINE),
}
_AMOUNT_RE = re.compile(r"payable amount is PKR ([\d,]+)", re.IGNORECASE)
_DUE_RE = re.compile(r"due date i\.e\.?\s*([0-9\-A-Za-z]+)", re.IGNORECASE)
_BILL_WORDS = ("bill", "bills", "invoice", "payment", "due", "payable", "submit", "reminder")


def _parse_email_block(body: str) -> dict[str, str]:
    info: dict[str, str] = {"body": body.strip()}
    for key, pattern in _HEADER_RE.items():
        match = pattern.search(body)
        if match:
            info[key] = match.group(1).strip()
    amount = _AMOUNT_RE.search(body)
    if amount:
        info["amount"] = f"PKR {amount.group(1)}"
    due = _DUE_RE.search(body)
    if due:
        info["due_date"] = due.group(1)
    return info


def _iter_gmail_blocks(scratch_lines: list[str]) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for entry in scratch_lines:
        match = _GMAIL_TOOL_RE.search(entry)
        if match:
            blocks.append((match.group(1), match.group(2).strip()))
    return blocks


def _looks_like_bill(email: dict[str, str]) -> bool:
    text = f"{email.get('subject', '')} {email.get('body', '')}".lower()
    return any(word in text for word in _BILL_WORDS)


def _format_email_line(email: dict[str, str], *, include_amount: bool) -> str:
    subject = email.get("subject") or "an email"
    bits = [subject]
    if email.get("from"):
        bits.append(f"from {email['from']}")
    if email.get("date"):
        bits.append(f"on {email['date']}")
    line = f"{bits[0]}, {', '.join(bits[1:])}" if len(bits) > 1 else bits[0]
    if include_amount and email.get("amount"):
        line += f" — amount {email['amount']}"
    if include_amount and email.get("due_date"):
        line += f", due {email['due_date']}"
    return line


def _parse_search_block(body: str) -> list[dict[str, str]]:
    emails: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                emails.append(current)
                current = {}
            continue
        if stripped.startswith("ID:"):
            if current:
                emails.append(current)
            current = {"id": stripped[3:].strip()}
        elif stripped.startswith("Subject:"):
            current["subject"] = stripped[8:].strip()
        elif stripped.startswith("From:"):
            current["from"] = stripped[5:].strip()
        elif stripped.startswith("Date:"):
            current["date"] = stripped[5:].strip()
    if current:
        emails.append(current)
    return emails


def build_factual_email_reply(scratch_lines: list[str], user_message: str) -> str | None:
    """Return a Jarvis reply when Gmail tools ran; None to fall back to the LLM."""
    blocks = _iter_gmail_blocks(scratch_lines)
    if not blocks:
        return None

    read_emails = [_parse_email_block(body) for tool, body in blocks if tool == "gmail__read_email"]
    search_emails: list[dict[str, str]] = []
    for tool, body in blocks:
        if tool == "gmail__search_emails":
            search_emails.extend(_parse_search_block(body))

    wants_bills = any(word in user_message.lower() for word in _BILL_WORDS)

    if read_emails:
        targets = [e for e in read_emails if _looks_like_bill(e)] if wants_bills else read_emails
        if wants_bills and not targets:
            if search_emails:
                lines = [_format_email_line(e, include_amount=False) for e in search_emails[:3]]
                return (
                    "I searched your inbox, sir, and found these bill-related messages: "
                    + "; ".join(lines)
                    + ". I couldn't read the full amount from the email body."
                )
            return "I checked your inbox, sir, but I didn't find a bill in the emails I read."

        lines = [_format_email_line(e, include_amount=True) for e in targets[:3]]
        if len(lines) == 1:
            return f"Yes sir — {lines[0]}."
        return "Sir, here's what I found in your inbox:\n" + "\n".join(f"• {line}" for line in lines)

    if search_emails:
        lines = [_format_email_line(e, include_amount=False) for e in search_emails[:5]]
        if not lines:
            return "I searched your inbox, sir, but didn't find any matching emails."
        if wants_bills:
            bill_lines = [line for line, email in zip(lines, search_emails) if _looks_like_bill(email)]
            if bill_lines:
                lines = bill_lines
        if len(lines) == 1:
            return f"I found one matching email, sir — {lines[0]}."
        return "Sir, I found these emails:\n" + "\n".join(f"• {line}" for line in lines)

    return None


def user_asks_about_email(user_message: str, history_messages: list[dict[str, Any]] | None = None) -> bool:
    lower = user_message.lower()
    if any(
        kw in lower
        for kw in (
            "email", "emails", "gmail", "inbox", "mail", "unread",
            "bill", "bills", "invoice", "payment", "stormfiber", "meezan",
        )
    ):
        return True
    if not history_messages:
        return False
    recent = " ".join(
        str(m.get("content", "") or "")
        for m in history_messages[-6:]
    ).lower()
    retry = any(
        kw in lower
        for kw in ("check again", "look again", "wrong", "missed", "not correct", "try again", "double check")
    )
    email_context = any(
        kw in recent
        for kw in ("email", "gmail", "inbox", "bill", "stormfiber", "meezan", "payment")
    )
    return retry and email_context
