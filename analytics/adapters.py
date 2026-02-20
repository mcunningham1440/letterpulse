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

    def add_message(self, request, level, message_template, message_context=None, message_text=None):
        """Suppress the 'Successfully signed in as' message."""
        if message_template and 'logged_in' in str(message_template):
            return
        super().add_message(request, level, message_template, message_context, message_text)

    def get_login_redirect_url(self, request):
        """Redirect to account page if user has no API credentials configured."""
        from .models import UsageAccount
        try:
            usage = UsageAccount.objects.get(user=request.user)
            if not usage.has_api_credentials:
                return '/account/'
        except UsageAccount.DoesNotExist:
            return '/account/'
        return super().get_login_redirect_url(request)
