from django import forms
from allauth.account.forms import SignupForm


class CustomSignupForm(SignupForm):
    first_name = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={'placeholder': 'First name', 'autofocus': 'autofocus'}),
    )
    last_name = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={'placeholder': 'Last name'}),
    )
    newsletter_name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. The Morning Brew'}),
    )

    field_order = ['first_name', 'last_name', 'newsletter_name', 'email', 'password1', 'password2']

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.save(update_fields=['first_name', 'last_name'])

        # Save newsletter name to UsageAccount (created by signal)
        if hasattr(user, 'usage_account'):
            user.usage_account.newsletter_name = self.cleaned_data['newsletter_name']
            user.usage_account.save(update_fields=['newsletter_name'])

        return user
