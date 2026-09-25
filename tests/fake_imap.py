"""A tiny Gmail IMAP stand-in that records what the client asked for."""

from __future__ import annotations

import imaplib
from email.message import EmailMessage


def make_email(sender: str, subject: str, body: str, date: str = "Fri, 25 Sep 2026 07:30:00 +0000",
               html: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Date"] = sender, subject, date
    msg.set_content(body, subtype="html" if html else "plain")
    return bytes(msg)


class FakeIMAP:
    def __init__(self, messages: list[tuple[bytes, bool]], password: str = "app-pass") -> None:
        self.messages = messages  # (raw, seen)
        self.password = password
        self.calls: list[tuple] = []

    def login(self, user, password):
        self.calls.append(("login", user))
        if password != self.password:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"ok"]

    def select(self, mailbox="INBOX", readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, msg_id, parts):
        self.calls.append(("fetch", parts))
        raw, seen = self.messages[int(msg_id) - 1]
        flags = b"\\Seen" if seen else b""
        if "RFC822.SIZE" in parts:
            return "OK", [msg_id + b" (FLAGS (" + flags + b") RFC822.SIZE " + str(len(raw)).encode() + b")"]
        return "OK", [(msg_id + b" (" + parts.strip("()").encode() + b" {%d}" % len(raw), raw), b")"]

    def logout(self):
        self.calls.append(("logout",))
