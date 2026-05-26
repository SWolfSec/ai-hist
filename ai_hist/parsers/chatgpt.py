from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai_hist.models import Event
from ai_hist.parsers.base import Parser

logger = logging.getLogger(__name__)

_KEYCHAIN_SERVICES = [
    "ChatGPT Safe Storage",
    "com.openai.chat Safe Storage",
    "Electron Safe Storage",
]

# Electron safeStorage on macOS: AES-128-CBC, key derived via PBKDF2-SHA1
# File header: b"v10" + 16-byte IV + ciphertext
_ELECTRON_PREFIX = b"v10"
_PBKDF2_SALT = b"saltysalt"
_PBKDF2_ITERATIONS = 1003
_KEY_LEN = 16


def _get_keychain_password() -> bytes | None:
    for svc in _KEYCHAIN_SERVICES:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", svc],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().encode()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def _derive_key(password: bytes) -> bytes:
    import hashlib
    return hashlib.pbkdf2_hmac("sha1", password, _PBKDF2_SALT, _PBKDF2_ITERATIONS, _KEY_LEN)


def _decrypt_file(path: Path, key: bytes) -> str | None:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        logger.debug("cryptography not installed; skipping ChatGPT decryption")
        return None

    try:
        raw = path.read_bytes()
        if not raw.startswith(_ELECTRON_PREFIX):
            return None
        iv = raw[3:19]
        ciphertext = raw[19:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        dec = cipher.decryptor()
        plaintext = dec.update(ciphertext) + dec.finalize()
        # PKCS7 unpadding
        pad = plaintext[-1]
        plaintext = plaintext[:-pad]
        return plaintext.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("Decryption failed for %s: %s", path, e)
        return None


class ChatGPTParser(Parser):
    name = "chatgpt"
    display_name = "ChatGPT Desktop"
    stable = False

    @property
    def _base(self) -> Path:
        return self.home / "Library" / "Application Support" / "com.openai.chat"

    def is_available(self) -> bool:
        return self._base.exists()

    def parse(self) -> list[Event]:
        if not self.is_available():
            return []

        key_bytes = _get_keychain_password()
        derived_key = _derive_key(key_bytes) if key_bytes else None

        events: list[Event] = []
        for data_file in self._base.rglob("conversations-v3-*/*.data"):
            stat = data_file.stat()
            ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            session_id = data_file.stem

            if derived_key:
                raw_text = _decrypt_file(data_file, derived_key)
                if raw_text:
                    events.extend(self._parse_conversation(raw_text, session_id, data_file))
                    continue

            # Can't decrypt; emit a stub so the file at least appears in the timeline
            events.append(Event(
                timestamp=ts,
                tool=self.name,
                role="system",
                content="[encrypted: decryption key unavailable]",
                session_id=session_id,
                source_file=data_file,
                metadata={"encrypted": True},
            ))
        return events

    def _parse_conversation(self, text: str, session_id: str, path: Path) -> list[Event]:
        events: list[Event] = []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []

        mapping = data.get("mapping", {})
        for node in mapping.values():
            msg = node.get("message")
            if not isinstance(msg, dict):
                continue
            author = msg.get("author", {}).get("role", "")
            if author not in ("user", "assistant"):
                continue
            content_obj = msg.get("content", {})
            parts = content_obj.get("parts", []) if isinstance(content_obj, dict) else []
            text_parts = [p for p in parts if isinstance(p, str) and p.strip()]
            if not text_parts:
                continue
            content = "\n".join(text_parts)

            created_ts = msg.get("create_time")
            ts = (
                datetime.fromtimestamp(created_ts, tz=timezone.utc)
                if isinstance(created_ts, (int, float))
                else datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            )
            events.append(Event(
                timestamp=ts,
                tool=self.name,
                role=author,
                content=content,
                session_id=session_id,
                source_file=path,
                metadata={},
            ))
        return events
