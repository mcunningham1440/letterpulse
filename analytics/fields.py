"""
Custom Django model fields for secure data storage.
"""

import base64
import hashlib
import logging
from django.conf import settings
from django.db import models
from cryptography.fernet import Fernet, InvalidToken


logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """
    Derive a Fernet-compatible encryption key from settings.BEEHIIV_TOKEN_ENCRYPTION_KEY.

    Uses SHA256 to hash the key and then base64-encodes the 32-byte digest to
    create a valid Fernet key. The source setting defaults to SECRET_KEY when
    BEEHIIV_TOKEN_ENCRYPTION_KEY is not configured, preserving readability of
    tokens originally encrypted under SECRET_KEY.
    """
    secret = settings.BEEHIIV_TOKEN_ENCRYPTION_KEY.encode('utf-8')
    # Use SHA256 to get consistent 32 bytes
    hashed = hashlib.sha256(secret).digest()
    # Fernet requires base64-encoded 32-byte key
    return base64.urlsafe_b64encode(hashed)


class EncryptedCharField(models.CharField):
    """
    A CharField that encrypts data at rest using Fernet symmetric encryption.

    The encryption key is derived from settings.BEEHIIV_TOKEN_ENCRYPTION_KEY
    (which defaults to SECRET_KEY when the env var is unset), so:
    - Changing the active key makes existing encrypted data unreadable
    - To rotate SECRET_KEY safely, first set BEEHIIV_TOKEN_ENCRYPTION_KEY to
      the current SECRET_KEY value, deploy, and only then rotate SECRET_KEY
    - Back up the active encryption key alongside database backups

    Usage:
        beehiiv_token = EncryptedCharField(max_length=500, blank=True, default='')

    Note: max_length should account for encryption overhead (~1.4x original size).
    """

    description = "An encrypted CharField using Fernet encryption"

    def __init__(self, *args, **kwargs):
        # Encryption adds overhead, so we need extra space
        # Fernet output is roughly: base64(IV + ciphertext + HMAC)
        # For a 255-char input, we need about 400 chars
        super().__init__(*args, **kwargs)

    def get_fernet(self) -> Fernet:
        """Get a Fernet instance for encryption/decryption."""
        return Fernet(get_encryption_key())

    def get_prep_value(self, value):
        """Encrypt value before saving to database."""
        if value is None or value == '':
            return value

        fernet = self.get_fernet()
        encrypted = fernet.encrypt(value.encode('utf-8'))
        return encrypted.decode('utf-8')

    def from_db_value(self, value, expression, connection):
        """Decrypt value when loading from database."""
        if value is None or value == '':
            return value

        if not self._is_encrypted(value):
            return value

        try:
            fernet = self.get_fernet()
            decrypted = fernet.decrypt(value.encode('utf-8'))
            return decrypted.decode('utf-8')
        except InvalidToken:
            logger.error(
                "EncryptedCharField: InvalidToken on decrypt for field=%s",
                getattr(self, 'name', '?'),
            )
            return ''

    def _is_encrypted(self, value: str) -> bool:
        """
        Check if a value appears to be Fernet-encrypted.

        Fernet tokens are base64-encoded and start with 'gAAAAA'
        (the base64 encoding of the version byte 0x80).
        """
        if not value or len(value) < 10:
            return False
        return value.startswith('gAAAAA')

    def deconstruct(self):
        """Support for migrations."""
        name, path, args, kwargs = super().deconstruct()
        return name, path, args, kwargs
