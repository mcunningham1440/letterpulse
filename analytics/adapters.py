from allauth.account.adapter import DefaultAccountAdapter


class NoSignupAccountAdapter(DefaultAccountAdapter):
    """Custom adapter that disables user signup."""

    def is_open_for_signup(self, request):
        """Return False to disable signup."""
        return False
