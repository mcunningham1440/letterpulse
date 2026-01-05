# Data migration to encrypt existing beehiiv tokens

from django.db import migrations


def encrypt_existing_tokens(apps, schema_editor):
    """
    Encrypt all existing beehiiv_token values.

    The EncryptedCharField automatically encrypts on save,
    so we just need to trigger a save on each record.
    """
    UsageAccount = apps.get_model('analytics', 'UsageAccount')

    # Import encryption utilities
    from analytics.fields import get_encryption_key
    from cryptography.fernet import Fernet

    fernet = Fernet(get_encryption_key())

    for account in UsageAccount.objects.all():
        if account.beehiiv_token and not account.beehiiv_token.startswith('gAAAAA'):
            # Token is not encrypted, encrypt it
            encrypted = fernet.encrypt(account.beehiiv_token.encode('utf-8'))
            account.beehiiv_token = encrypted.decode('utf-8')
            account.save(update_fields=['beehiiv_token'])


def decrypt_tokens_for_rollback(apps, schema_editor):
    """
    Decrypt tokens when rolling back this migration.

    Note: This is provided for rollback purposes, but tokens
    will remain encrypted until re-saved after rollback.
    """
    UsageAccount = apps.get_model('analytics', 'UsageAccount')

    from analytics.fields import get_encryption_key
    from cryptography.fernet import Fernet, InvalidToken

    fernet = Fernet(get_encryption_key())

    for account in UsageAccount.objects.all():
        if account.beehiiv_token and account.beehiiv_token.startswith('gAAAAA'):
            try:
                decrypted = fernet.decrypt(account.beehiiv_token.encode('utf-8'))
                account.beehiiv_token = decrypted.decode('utf-8')
                account.save(update_fields=['beehiiv_token'])
            except InvalidToken:
                # If decryption fails, leave as-is
                pass


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0014_encrypt_beehiiv_token'),
    ]

    operations = [
        migrations.RunPython(
            encrypt_existing_tokens,
            decrypt_tokens_for_rollback,
        ),
    ]
