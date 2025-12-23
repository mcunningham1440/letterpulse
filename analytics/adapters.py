from allauth.account.adapter import DefaultAccountAdapter


class NoSignupAccountAdapter(DefaultAccountAdapter):
    """Custom adapter that disables user signup and normalizes emails."""

    def is_open_for_signup(self, request):
        """Return False to disable signup."""
        return False

    def clean_email(self, email):
        """Normalize email to lowercase to ensure case-insensitive lookups work."""
        email = super().clean_email(email)
        return email.lower() if email else email
