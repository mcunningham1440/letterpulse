from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom adapter that auto-generates usernames and normalizes emails."""

    def is_open_for_signup(self, request):
        cap = getattr(settings, 'DAILY_SIGNUP_CAP', None)
        if cap is not None:
            since = timezone.now() - timezone.timedelta(hours=24)
            recent = User.objects.filter(date_joined__gte=since).count()
            if recent >= cap:
                return False
        return True

    def _unique_username_from_email(self, email):
        """Generate a unique username from the email local part."""
        base = email.split('@')[0].lower()
        if not User.objects.filter(username=base).exists():
            return base
        counter = 1
        while User.objects.filter(username=f"{base}{counter}").exists():
            counter += 1
        return f"{base}{counter}"

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        user.username = self._unique_username_from_email(user.email)
        if commit:
            user.save()
        return user

    def clean_email(self, email):
        """Normalize email to lowercase to ensure case-insensitive lookups work."""
        email = super().clean_email(email)
        return email.lower() if email else email

    def add_message(self, request, level, message_template, message_context=None, message_text=None):
        """Suppress the 'Successfully signed in as' and email confirmation messages."""
        if message_template:
            template_str = str(message_template)
            if 'logged_in' in template_str or 'email_confirmation_sent' in template_str:
                return
        super().add_message(request, level, message_template, message_context, message_text)

    def get_login_redirect_url(self, request):
        """Redirect to account page if user has no API credentials configured."""
        from .models import UsageAccount
        try:
            usage = UsageAccount.objects.get(user=request.user)
            if not usage.has_api_credentials:
                return '/account/?setup=configure'
        except UsageAccount.DoesNotExist:
            return '/account/?setup=configure'
        return super().get_login_redirect_url(request)

    def get_signup_redirect_url(self, request):
        """Redirect new signups to account page to configure API credentials."""
        return '/account/?setup=configure'
