"""
Public, unauthenticated entry points: the about-page index and the
mobile-not-supported notice.
"""

from django.shortcuts import redirect, render


def index(request):
    """Show about page for unauthenticated users, redirect to Write for authenticated users"""
    if request.user.is_authenticated:
        return redirect('analytics:insights')
    return render(request, 'analytics/about.html')


def mobile_notice(request):
    """Show a notice that the app is not optimized for mobile"""
    return render(request, 'analytics/mobile.html')
