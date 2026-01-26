"""
Views for the analytics app.
"""

import re
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
import pandas as pd
import json
import ast
from asgiref.sync import async_to_sync

from .models import Post, ContentSet, Report, UsageAccount


# =============================================================================
# Security Helpers
# =============================================================================

def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent HTTP header injection and path traversal.

    Removes or replaces characters that could be used for:
    - HTTP header injection (newlines, carriage returns)
    - Path traversal (slashes, backslashes)
    - Shell injection (quotes, semicolons)
    """
    if not filename:
        return "download"

    # Remove any path components
    filename = filename.replace('/', '_').replace('\\', '_')

    # Remove characters that could cause header injection or other issues
    # Keep only alphanumeric, spaces, hyphens, underscores, and periods
    sanitized = re.sub(r'[^\w\s\-.]', '', filename)

    # Collapse multiple spaces/underscores
    sanitized = re.sub(r'[\s_]+', '_', sanitized)

    # Remove leading/trailing underscores and periods
    sanitized = sanitized.strip('_.')

    # Ensure we have a valid filename
    if not sanitized:
        return "download"

    # Limit length to prevent issues
    return sanitized[:200]


# Pattern for validating content set names
# Allows alphanumeric, spaces, hyphens, underscores, and common punctuation
VALID_SET_NAME_PATTERN = re.compile(r'^[\w\s\-.,()\']+$', re.UNICODE)

def validate_set_name(name: str) -> tuple[bool, str]:
    """
    Validate a content set name for security and usability.

    Returns:
        (is_valid, error_message) tuple
    """
    if not name:
        return False, "Name cannot be empty."

    if len(name) > 200:
        return False, "Name must be 200 characters or less."

    if len(name) < 1:
        return False, "Name must be at least 1 character."

    # Check for newlines and other control characters
    if any(c in name for c in '\n\r\t\x00'):
        return False, "Name cannot contain control characters."

    # Check against allowed pattern
    if not VALID_SET_NAME_PATTERN.match(name):
        return False, "Name can only contain letters, numbers, spaces, hyphens, underscores, periods, commas, parentheses, and apostrophes."

    return True, ""


from .utils import (
    load_posts_from_db,
    fetch_posts_html_and_clicks_parallel,
    extract_items_parallel,
    generate_content_insights,
    refresh_posts_data,
    annotate_posts_parallel,
    NotEnoughCredits,
    charge_credits,
    validate_beehiiv_api_key,
)


import functools


def get_user_api_credentials(user):
    """
    Get the Beehiiv API credentials for a user.
    Returns (token, pub_id, is_valid) tuple.
    """
    try:
        usage = UsageAccount.objects.get(user=user)
        if usage.has_api_credentials:
            return usage.beehiiv_token, usage.beehiiv_pub_id, usage.api_key_valid
    except UsageAccount.DoesNotExist:
        pass
    return None, None, False


def require_valid_api_credentials(view_func):
    """
    Decorator that checks for valid API credentials before allowing access.
    Redirects to account page with error message if credentials are missing or invalid.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        token, pub_id, is_valid = get_user_api_credentials(request.user)

        if not token or not pub_id:
            messages.error(request, "Please configure your Beehiiv API credentials in your Account settings.")
            return redirect('analytics:account')

        if not is_valid:
            messages.error(request, "Your API key is invalid. Please update it in your Account settings.")
            return redirect('analytics:account')

        return view_func(request, *args, **kwargs)

    return wrapper


def index(request):
    """Show about page for unauthenticated users, redirect to posts for authenticated users"""
    if request.user.is_authenticated:
        return redirect('analytics:posts')
    return render(request, 'analytics/about.html')


@login_required
def account_view(request):
    """
    Display and manage user account settings including API credentials and usage.
    """
    from .models import Publication

    # Get or create usage account
    usage, created = UsageAccount.objects.get_or_create(
        user=request.user,
        defaults={'period_start': timezone.now().date().replace(day=1)}
    )
    usage.ensure_current_period()

    if request.method == 'POST':
        action = request.POST.get('action')
        old_pub_id = usage.beehiiv_pub_id  # Track for clearing session data on change

        if action == 'update_credentials':
            beehiiv_token = request.POST.get('beehiiv_token', '').strip()
            beehiiv_pub_id = request.POST.get('beehiiv_pub_id', '').strip()

            if beehiiv_token:
                # Validate the API key against Beehiiv
                is_valid, result = async_to_sync(validate_beehiiv_api_key)(beehiiv_token)

                if is_valid:
                    # Store token and publications list
                    usage.beehiiv_token = beehiiv_token
                    usage.api_key_valid = True
                    usage.available_publications = result  # List of publication dicts

                    # Validate selected publication is in list
                    valid_pub_ids = [p["id"] for p in result]
                    if beehiiv_pub_id and beehiiv_pub_id in valid_pub_ids:
                        usage.beehiiv_pub_id = beehiiv_pub_id
                    elif result:
                        # Default to first publication if none selected or invalid
                        usage.beehiiv_pub_id = result[0]["id"]
                    else:
                        usage.beehiiv_pub_id = ''

                    usage.save()

                    # Ensure Publication record exists for the selected pub
                    if usage.beehiiv_pub_id:
                        pub_data = next((p for p in result if p["id"] == usage.beehiiv_pub_id), None)
                        if pub_data:
                            Publication.objects.update_or_create(
                                pub_id=usage.beehiiv_pub_id,
                                defaults={
                                    'name': pub_data.get('name', 'Unknown'),
                                    'organization_name': pub_data.get('organization_name', '')
                                }
                            )

                    # Clear extracted items if publication changed
                    if usage.beehiiv_pub_id != old_pub_id:
                        request.session.pop('extracted_items', None)

                    messages.success(request, "API credentials validated and saved!")
                else:
                    # Invalid key
                    usage.beehiiv_token = beehiiv_token  # Keep token so user can see it
                    usage.api_key_valid = False
                    usage.available_publications = []
                    usage.save()
                    messages.error(request, f"API key validation failed: {result}")
            else:
                # Clear credentials and extracted items
                usage.beehiiv_token = ''
                usage.beehiiv_pub_id = ''
                usage.api_key_valid = False
                usage.available_publications = []
                usage.save()
                request.session.pop('extracted_items', None)
                messages.info(request, "API credentials cleared.")

            return redirect('analytics:account')

        elif action == 'update_timezone':
            # Handle timezone update
            from .utils import TIMEZONE_CHOICES
            new_timezone = request.POST.get('timezone', '').strip()
            valid_timezones = [tz[0] for tz in TIMEZONE_CHOICES]
            if new_timezone in valid_timezones:
                usage.timezone = new_timezone
                usage.save(update_fields=['timezone'])
                messages.success(request, "Timezone updated successfully!")
            else:
                messages.error(request, "Invalid timezone selected.")
            return redirect('analytics:account')

        elif action == 'switch_publication':
            # Handle publication switching
            new_pub_id = request.POST.get('beehiiv_pub_id', '').strip()
            valid_pub_ids = [p["id"] for p in usage.available_publications]

            if new_pub_id in valid_pub_ids and new_pub_id != old_pub_id:
                usage.beehiiv_pub_id = new_pub_id
                usage.save()

                # Clear extracted items since they belong to old publication
                request.session.pop('extracted_items', None)

                # Ensure Publication record exists
                pub_data = next((p for p in usage.available_publications if p["id"] == new_pub_id), None)
                if pub_data:
                    Publication.objects.update_or_create(
                        pub_id=new_pub_id,
                        defaults={
                            'name': pub_data.get('name', 'Unknown'),
                            'organization_name': pub_data.get('organization_name', '')
                        }
                    )

                messages.success(request, "Publication switched successfully!")
            elif new_pub_id == old_pub_id:
                pass  # No change needed
            else:
                messages.error(request, "Invalid publication selected.")

            return redirect('analytics:account')

    from .utils import TIMEZONE_CHOICES
    context = {
        'usage': usage,
        'timezone_choices': TIMEZONE_CHOICES,
    }

    return render(request, 'analytics/account.html', context)


# Import timezone for account_view
from django.utils import timezone


@login_required
@require_valid_api_credentials
def posts_view(request):
    """
    Display the posts page with posts table.
    """
    from .utils import convert_to_user_timezone
    # from .models import Publication

    # Get current publication for filtering
    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

    # Get user timezone for date formatting
    usage = UsageAccount.objects.get(user=request.user)
    user_tz = usage.timezone

    
    ### Demo mode custom BAIA adjustments
    # Get publication name for demo mode check
    from .models import Publication
    publication_name = None
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        publication_name = publication.name
    except Publication.DoesNotExist:
        pass
    ###


    # Load posts from database filtered by publication and user
    posts_df = load_posts_from_db(publication_id=beehiiv_pub_id, user=request.user)

    # Reverse order so newer posts appear first
    posts_df = posts_df.iloc[::-1].reset_index(drop=True)


    ### Demo mode custom BAIA adjustments
    if publication_name == "Building AI Agents" and not posts_df.empty and request.user.username == "jackstone":
        # Hide post titled "Farewell, and thank you"
        posts_df = posts_df[posts_df['title'] != "Farewell, and thank you"].reset_index(drop=True)
        # Make "Amazon's Shopping Agent Controversy" appear as Scheduled with tomorrow's date and zero stats
        amazon_mask = posts_df['title'].str.contains("Amazon.*Shopping Agent Controversy", regex=True, na=False)
        posts_df.loc[amazon_mask, 'status'] = "Scheduled"
        from datetime import timedelta
        tomorrow = pd.Timestamp.now('UTC') + timedelta(days=1)
        posts_df.loc[amazon_mask, 'publish_date'] = tomorrow
        posts_df.loc[amazon_mask, 'recipients'] = 0
        posts_df.loc[amazon_mask, 'unique_email_opens'] = 0
        posts_df.loc[amazon_mask, 'unique_email_clicks'] = 0
        # Multiply opens by 1.2 and clicks by 3, rounded to nearest int
        posts_df['unique_email_opens'] = (posts_df['unique_email_opens'] * 1.2).round().astype(int)
        posts_df['unique_email_clicks'] = (posts_df['unique_email_clicks'] * 3).round().astype(int)
        # Apply growth factor based on days since November 1, 2025 for each post
        from datetime import datetime
        baseline = datetime(2025, 11, 1, tzinfo=pd.Timestamp.now('UTC').tzinfo)
        posts_df['days_elapsed'] = (pd.to_datetime(posts_df['publish_date']) - baseline).dt.days.fillna(0).clip(lower=0)
        posts_df['growth_factor'] = (1.006 ** posts_df['days_elapsed']).clip(lower=1.0)
        posts_df['recipients'] = (posts_df['recipients'].fillna(0) * posts_df['growth_factor']).round().astype(int)
        posts_df['unique_email_opens'] = (posts_df['unique_email_opens'].fillna(0) * posts_df['growth_factor']).round().astype(int)
        posts_df['unique_email_clicks'] = (posts_df['unique_email_clicks'].fillna(0) * posts_df['growth_factor']).round().astype(int)
        posts_df = posts_df.drop(columns=['days_elapsed', 'growth_factor'])
    ###


    # Convert to list of dicts for template
    posts_data = posts_df.to_dict('records')

    # Format dates for display with user's timezone
    for post in posts_data:
        # Format publish_date
        local_dt = convert_to_user_timezone(post.get('publish_date'), user_tz)
        if local_dt:
            post['publish_date_display'] = local_dt.strftime('%b %d, %Y')
            post['publish_date_sortable'] = local_dt.strftime('%Y-%m-%d')
        else:
            post['publish_date_display'] = '-'
            post['publish_date_sortable'] = ''

        # Format creation_date
        local_dt = convert_to_user_timezone(post.get('creation_date'), user_tz)
        if local_dt:
            post['creation_date_display'] = local_dt.strftime('%b %d, %Y')
            post['creation_date_sortable'] = local_dt.strftime('%Y-%m-%d')
        else:
            post['creation_date_display'] = '-'
            post['creation_date_sortable'] = ''
    
    # Get extracted items from session if available
    extracted_items = request.session.get('extracted_items', None)
    extracted_items_data = None
    
    if extracted_items:
        df = pd.DataFrame(extracted_items)
        # Parse lists if they're stored as strings
        for col in ['clicks', 'links', 'click_rate']:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda x: ast.literal_eval(x) if isinstance(x, str) else x
                )
        
        # Calculate max clicks and max click rate before formatting
        df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
        df['max_click_rate'] = df['click_rate'].apply(
            lambda x: f"{max(x) * 100:.2f}%" if x and max(x) > 0 else "0.00%"
        )
        
        # Format click rates as percentages
        df['click_rate'] = df['click_rate'].apply(
            lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
        )

        # Format post_date for display (stored as string in session)
        if 'post_date' in df.columns:
            from datetime import datetime as dt

            def format_post_date(date_str):
                if not date_str or date_str == 'None' or date_str == 'NaT':
                    return '-', ''
                try:
                    # Parse the date string (may be in various formats)
                    if 'T' in str(date_str) or '+' in str(date_str):
                        # ISO format with time/timezone
                        parsed = pd.to_datetime(date_str, utc=True)
                        local_dt = convert_to_user_timezone(parsed, user_tz)
                    else:
                        # Simple date format
                        parsed = dt.strptime(str(date_str)[:10], '%Y-%m-%d')
                        local_dt = parsed
                    return local_dt.strftime('%b %d, %Y'), local_dt.strftime('%Y-%m-%d')
                except Exception:
                    return str(date_str), str(date_str)

            formatted = df['post_date'].apply(format_post_date)
            df['post_date_display'] = formatted.apply(lambda x: x[0])
            df['post_date_sortable'] = formatted.apply(lambda x: x[1])

        extracted_items_data = df.to_dict('records')
    
    # Get content sets for current publication and user only
    from .models import Publication
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        all_content_sets = ContentSet.objects.filter(publication=publication, user=request.user).order_by('name')
        all_reports = Report.objects.filter(content_set__publication=publication, content_set__user=request.user).order_by('name')
    except Publication.DoesNotExist:
        all_content_sets = ContentSet.objects.none()
        all_reports = Report.objects.none()
    
    context = {
        'posts': posts_data,
        'extracted_items': extracted_items_data,
        'all_content_sets': all_content_sets,
        'all_reports': all_reports,
    }
    
    return render(request, 'analytics/posts.html', context)


@login_required
@require_POST
@require_valid_api_credentials
def run_extraction(request):
    """
    Run content extraction on selected posts.
    """
    try:
        # Get API credentials (already validated by decorator)
        beehiiv_token, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        # Get form data
        selected_indices = request.POST.getlist('selected_posts')
        content_desc = request.POST.get('content_desc', '').strip()

        if not content_desc:
            messages.error(request, "Please provide a content description.")
            return redirect('analytics:posts')

        if not selected_indices:
            messages.error(request, "Please select at least one post.")
            return redirect('analytics:posts')

        # Convert indices to integers
        selected_indices = [int(idx) for idx in selected_indices]

        # Load data from database filtered by publication and user
        posts_df = load_posts_from_db(publication_id=beehiiv_pub_id, user=request.user)
        posts_df = posts_df.iloc[::-1].reset_index(drop=True)  # Reverse to match display

        # Get selected posts
        posts_of_interest = posts_df.iloc[selected_indices]

        # Extract post IDs for fetching HTMLs and clicks
        post_ids = posts_of_interest['id'].tolist()

        # Fetch HTMLs and clicks dynamically from API in parallel
        htmls, clicks_by_id = async_to_sync(fetch_posts_html_and_clicks_parallel)(post_ids, beehiiv_token, beehiiv_pub_id)
        
        if not htmls:
            messages.error(request, "Failed to fetch content from Beehiiv.")
            return redirect('analytics:posts')
        
        # Prepare data for parallel extraction (now includes post_id)
        posts_data = [
            (row.id, row.title, f"{row.id}.html", row.publish_date, row.unique_email_opens)
            for _, row in posts_of_interest.iterrows()
        ]
        
        # Check for missing HTML or clicks data
        missing_html = [
            title for post_id, title, html_file, _, _ in posts_data 
            if html_file not in htmls
        ]
        missing_clicks = [
            title for post_id, title, _, _, _ in posts_data 
            if post_id not in clicks_by_id
        ]
        
        if missing_html:
            for title in missing_html:
                messages.warning(request, f"Failed to fetch HTML for post: {title}")
        if missing_clicks:
            for title in missing_clicks:
                messages.warning(request, f"Failed to fetch click data for post: {title}")

        # Charge credits before extraction (1 credit per post)
        num_posts = len([p for p in posts_data if p[2] in htmls and p[0] in clicks_by_id])
        credits_needed = num_posts * settings.CREDITS_PER_EXTRACTION
        charge_credits(request.user, credits_needed)

        # Run async extraction
        items_list = async_to_sync(extract_items_parallel)(
            posts_data,
            content_desc,
            htmls,
            clicks_by_id,
            user=request.user
        )
        
        if items_list:
            extracted_df = pd.concat(items_list, ignore_index=True)
            
            # Convert date objects to strings for JSON serialization
            if 'post_date' in extracted_df.columns:
                extracted_df['post_date'] = extracted_df['post_date'].astype(str)
            
            # Store in session as list of dicts
            request.session['extracted_items'] = extracted_df.to_dict('records')
            messages.success(request, f"Successfully extracted {len(extracted_df)} items from {len(items_list)} posts!")
        else:
            messages.warning(request, "No items were extracted.")

    except NotEnoughCredits as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Error during extraction: {str(e)}")

    return redirect('analytics:posts')


@login_required
@require_POST
def delete_items(request):
    """
    Delete selected items from extracted items.
    """
    try:
        items_to_delete = request.POST.getlist('items_to_delete')
        
        if not items_to_delete:
            messages.warning(request, "No items selected for deletion.")
            return redirect('analytics:posts')
        
        # Convert to integers
        items_to_delete = [int(idx) for idx in items_to_delete]
        
        # Get current extracted items
        extracted_items = request.session.get('extracted_items', [])
        
        if not extracted_items:
            messages.error(request, "No extracted items found.")
            return redirect('analytics:posts')
        
        # Remove selected items
        extracted_items = [
            item for i, item in enumerate(extracted_items) 
            if i not in items_to_delete
        ]
        
        # Update session
        request.session['extracted_items'] = extracted_items
        messages.success(request, f"Removed {len(items_to_delete)} item(s).")
        
    except Exception as e:
        messages.error(request, f"Error deleting items: {str(e)}")

    return redirect('analytics:posts')


@login_required
def download_extracted_csv(request):
    """
    Download the current extracted items from session as CSV.
    """
    extracted_items = request.session.get('extracted_items', [])

    if not extracted_items:
        messages.error(request, "No extracted items to download.")
        return redirect('analytics:posts')

    try:
        df = pd.DataFrame(extracted_items)

        # Calculate max clicks and max click rate before formatting
        if 'clicks' in df.columns:
            df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
        else:
            df['max_clicks'] = 0

        if 'click_rate' in df.columns:
            df['max_click_rate'] = df['click_rate'].apply(
                lambda x: f"{max(x) * 100:.2f}%" if x and max(x) > 0 else "0.00%"
            )
            # Format click rates as percentages
            df['click_rate'] = df['click_rate'].apply(
                lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
            )
        else:
            df['max_click_rate'] = "0.00%"

        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="extracted_items.csv"'

        df.to_csv(response, index=False)

        return response

    except Exception as e:
        messages.error(request, f"Error downloading CSV: {str(e)}")
        return redirect('analytics:posts')


@login_required
@require_POST
@require_valid_api_credentials
def save_content_set(request):
    """
    Save extracted items as a named content set or add to existing set.
    """
    from .models import Publication

    try:
        set_mode = request.POST.get('set_mode', 'create')

        # Get current publication
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        except Publication.DoesNotExist:
            messages.error(request, "Publication not found. Please refresh your posts first.")
            return redirect('analytics:posts')

        # Get extracted items from session
        extracted_items = request.session.get('extracted_items', [])

        if not extracted_items:
            messages.error(request, "No extracted items to save.")
            return redirect('analytics:posts')

        if set_mode == 'create':
            # Create new content set
            content_set_name = request.POST.get('content_set_name', '').strip()

            if not content_set_name:
                messages.error(request, "Please provide a name for the content set.")
                return redirect('analytics:posts')

            # Validate the content set name for security
            is_valid, error_msg = validate_set_name(content_set_name)
            if not is_valid:
                messages.error(request, error_msg)
                return redirect('analytics:posts')

            # Check if name already exists for this publication and user
            if ContentSet.objects.filter(name=content_set_name, publication=publication, user=request.user).exists():
                messages.error(request, f"A content set named '{content_set_name}' already exists. Please choose a different name.")
                return redirect('analytics:posts')

            # Create ContentSet
            df = pd.DataFrame(extracted_items)
            content_set = ContentSet.from_dataframe(content_set_name, df)
            content_set.publication = publication
            content_set.user = request.user
            content_set.save()
            
            messages.success(request, f"Content set '{content_set_name}' saved successfully!")
            
        elif set_mode == 'add':
            # Add to existing content set
            existing_set_name = request.POST.get('existing_set_name', '').strip()
            keep_copy = request.POST.get('keep_copy') == 'true'
            
            if not existing_set_name:
                messages.error(request, "Please select an existing content set.")
                return redirect('analytics:posts')

            # Get the existing content set (owned by this user)
            try:
                existing_set = ContentSet.objects.get(name=existing_set_name, user=request.user)
            except ContentSet.DoesNotExist:
                messages.error(request, f"Content set '{existing_set_name}' not found.")
                return redirect('analytics:posts')
            
            # Keep a copy of the old set if requested
            if keep_copy:
                import datetime
                backup_name = f"{existing_set_name} copy"

                # Create a backup copy
                backup_set = ContentSet(
                    name=backup_name,
                    description=f"Backup of '{existing_set_name}' before adding items",
                    items_data=existing_set.items_data,
                    publication=publication,
                    user=request.user
                )
                backup_set.save()
                messages.info(request, f"Backup created: '{backup_name}'")
            
            # Merge the new items with existing items
            existing_items = existing_set.items_data if isinstance(existing_set.items_data, list) else []
            new_items_df = pd.DataFrame(extracted_items)
            
            if existing_items:
                existing_df = pd.DataFrame(existing_items)
                # Concatenate the dataframes
                combined_df = pd.concat([existing_df, new_items_df], ignore_index=True)
            else:
                combined_df = new_items_df
            
            # Update the existing content set
            existing_set.items_data = combined_df.to_dict(orient='records')
            existing_set.save()
            
            messages.success(request, f"Added {len(extracted_items)} items to content set '{existing_set_name}'!")
        
        # Clear extracted items from session
        request.session.pop('extracted_items', None)
        
    except Exception as e:
        messages.error(request, f"Error saving content set: {str(e)}")
    
    return redirect('analytics:posts')


def _load_stopwords(num_words=200):
    """
    Load top N most common words from english_word_frequencies.csv.
    The file is pre-sorted by rank, so we just read the first N rows.
    """
    import csv
    import os
    csv_path = os.path.join(os.path.dirname(__file__), 'data', 'english_word_frequencies.csv')

    stopwords = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = row.get('word', '').lower().strip()
            if word:
                stopwords.append(word)
                if len(stopwords) >= num_words:
                    break
    return stopwords

# Cache stopwords at module level (loaded once at app startup)
_CACHED_STOPWORDS = None

def get_stopwords():
    global _CACHED_STOPWORDS
    if _CACHED_STOPWORDS is None:
        _CACHED_STOPWORDS = _load_stopwords()
    return _CACHED_STOPWORDS


@login_required
@require_valid_api_credentials
def insights_view(request):
    """
    Display the insights page with content sets.
    """
    from .models import Publication
    import json

    # Get current publication for filtering
    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

    # Get content sets for current publication and user only
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        content_sets = ContentSet.objects.filter(publication=publication, user=request.user).order_by('-created_at')
        has_reports = Report.objects.filter(
            content_set__publication=publication,
            content_set__user=request.user
        ).exists()
    except Publication.DoesNotExist:
        content_sets = ContentSet.objects.none()
        has_reports = False

    context = {
        'content_sets': content_sets,
        'has_reports': has_reports,
        'stopwords_json': json.dumps(get_stopwords()),
    }

    return render(request, 'analytics/insights.html', context)


@login_required
def load_content_set(request, set_name):
    """
    Load a specific content set and return as JSON.
    """
    try:
        content_set = ContentSet.objects.get(name=set_name, user=request.user)
        df = content_set.to_dataframe()

        # Get user timezone for date formatting
        usage = UsageAccount.objects.get(user=request.user)
        user_tz = usage.timezone

        # Format for display
        if not df.empty:
            # Calculate max clicks and max click rate before formatting
            df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
            df['max_click_rate'] = df['click_rate'].apply(
                lambda x: f"{max(x) * 100:.2f}%" if x and max(x) > 0 else "0.00%"
            )

            # Preserve raw click_rate for phrase analysis before formatting
            df['click_rate_raw'] = df['click_rate'].apply(lambda x: x if x else [])

            # Format click rates as percentages
            df['click_rate'] = df['click_rate'].apply(
                lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
            )

            # Format post_date for display
            if 'post_date' in df.columns:
                from datetime import datetime as dt

                def format_date(d):
                    if d is None:
                        return '-', ''
                    try:
                        if hasattr(d, 'strftime'):
                            # Already a date/datetime object
                            return d.strftime('%b %d, %Y'), d.strftime('%Y-%m-%d')
                        else:
                            # String - parse it
                            parsed = dt.strptime(str(d)[:10], '%Y-%m-%d')
                            return parsed.strftime('%b %d, %Y'), parsed.strftime('%Y-%m-%d')
                    except Exception:
                        return str(d), str(d)

                formatted = df['post_date'].apply(format_date)
                df['post_date_display'] = formatted.apply(lambda x: x[0])
                df['post_date_sortable'] = formatted.apply(lambda x: x[1])

            data = df.to_dict('records')
        else:
            data = []

        return JsonResponse({
            'success': True,
            'name': content_set.name,
            'data': data,
        })
        
    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{set_name}' not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def generate_insights(request):
    """
    Generate AI insights for a content set.
    """
    try:
        set_name = request.POST.get('set_name')

        if not set_name:
            return JsonResponse({
                'success': False,
                'error': 'Content set name is required.'
            }, status=400)

        content_set = ContentSet.objects.get(name=set_name, user=request.user)
        df = content_set.to_dataframe()

        if df.empty:
            return JsonResponse({
                'success': False,
                'error': 'Content set is empty.'
            }, status=400)

        # Charge credits before generating report (flat cost)
        charge_credits(request.user, settings.CREDITS_PER_REPORT)

        # Generate insights using async function
        response = async_to_sync(generate_content_insights)(df, user=request.user)
        insights = response.output[-1].content[0].text

        # Get updated usage for sidebar
        from .models import UsageAccount
        usage = UsageAccount.objects.get(user=request.user)
        usage.ensure_current_period()

        return JsonResponse({
            'success': True,
            'insights': insights,
            'credits_used': usage.used_this_period,
            'credits_quota': usage.monthly_quota,
        })

    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{set_name}' not found."
        }, status=404)
    except NotEnoughCredits as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=402)  # Payment Required
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def rename_set(request):
    """
    Rename a content set.
    """
    try:
        old_name = request.POST.get('old_name')
        new_name = request.POST.get('new_name')
        
        if not old_name or not new_name:
            return JsonResponse({
                'success': False,
                'error': 'Both old and new names are required.'
            }, status=400)

        # Validate the new name for security
        is_valid, error_msg = validate_set_name(new_name)
        if not is_valid:
            return JsonResponse({
                'success': False,
                'error': error_msg
            }, status=400)

        # Check if new name already exists for this user
        if ContentSet.objects.filter(name=new_name, user=request.user).exists():
            return JsonResponse({
                'success': False,
                'error': f"A content set named '{new_name}' already exists."
            }, status=400)

        content_set = ContentSet.objects.get(name=old_name, user=request.user)
        content_set.name = new_name
        content_set.save()
        
        return JsonResponse({
            'success': True,
            'message': f"Content set renamed to '{new_name}'."
        })
        
    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{old_name}' not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def copy_set(request):
    """
    Create a copy of a content set.
    """
    try:
        set_name = request.POST.get('set_name')
        
        if not set_name:
            return JsonResponse({
                'success': False,
                'error': 'Content set name is required.'
            }, status=400)

        content_set = ContentSet.objects.get(name=set_name, user=request.user)
        copy_name = set_name + ' copy'

        # Find a unique name if copy already exists
        counter = 2
        while ContentSet.objects.filter(name=copy_name, user=request.user).exists():
            copy_name = f"{set_name} copy {counter}"
            counter += 1
        
        # Create the copy with items_data
        import copy as copy_module
        copy_set = ContentSet.objects.create(
            name=copy_name,
            description=content_set.description,
            items_data=copy_module.deepcopy(content_set.items_data),
            publication=content_set.publication,
            user=request.user
        )
        
        return JsonResponse({
            'success': True,
            'message': f"Content set copied as '{copy_name}'.",
            'copy_name': copy_name
        })
        
    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{set_name}' not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def merge_sets(request):
    """
    Merge one content set into another.
    """
    try:
        source_set_name = request.POST.get('source_set')
        target_set_name = request.POST.get('target_set')
        
        if not source_set_name or not target_set_name:
            return JsonResponse({
                'success': False,
                'error': 'Both source and target set names are required.'
            }, status=400)

        source_set = ContentSet.objects.get(name=source_set_name, user=request.user)
        target_set = ContentSet.objects.get(name=target_set_name, user=request.user)
        
        # Merge items_data from source to target
        source_items = source_set.items_data if isinstance(source_set.items_data, list) else []
        target_items = target_set.items_data if isinstance(target_set.items_data, list) else []
        
        # Extend target with source items
        target_items.extend(source_items)
        target_set.items_data = target_items
        target_set.save()
        
        items_added = len(source_items)
        
        return JsonResponse({
            'success': True,
            'message': f"Merged {items_added} items from '{source_set_name}' into '{target_set_name}'.",
            'items_added': items_added
        })
        
    except ContentSet.DoesNotExist as e:
        return JsonResponse({
            'success': False,
            'error': 'One or both content sets not found.'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def delete_items_from_set(request):
    """
    Delete specific items from a content set by their indices.
    """
    try:
        set_name = request.POST.get('set_name')
        indices_json = request.POST.get('indices')
        
        if not set_name or not indices_json:
            return JsonResponse({
                'success': False,
                'error': 'Set name and item indices are required.'
            }, status=400)

        indices = json.loads(indices_json)

        content_set = ContentSet.objects.get(name=set_name, user=request.user)
        items = content_set.items_data if isinstance(content_set.items_data, list) else []
        
        # Validate indices
        if not all(0 <= idx < len(items) for idx in indices):
            return JsonResponse({
                'success': False,
                'error': 'Invalid item indices.'
            }, status=400)
        
        # Delete items at specified indices (in reverse order to maintain correct indices)
        indices_sorted = sorted(indices, reverse=True)
        for idx in indices_sorted:
            items.pop(idx)
        
        content_set.items_data = items
        content_set.save()
        
        return JsonResponse({
            'success': True,
            'message': f"Deleted {len(indices)} item(s) from '{set_name}'."
        })
        
    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{set_name}' not found."
        }, status=404)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid indices format.'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def delete_set(request):
    """
    Delete an entire content set.
    """
    try:
        set_name = request.POST.get('set_name')
        
        if not set_name:
            return JsonResponse({
                'success': False,
                'error': 'Content set name is required.'
            }, status=400)

        content_set = ContentSet.objects.get(name=set_name, user=request.user)
        content_set.delete()
        
        return JsonResponse({
            'success': True,
            'message': f"Content set '{set_name}' deleted successfully."
        })
        
    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{set_name}' not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def download_csv(request, set_name):
    """
    Download a content set as CSV.
    """
    try:
        content_set = ContentSet.objects.get(name=set_name, user=request.user)
        df = content_set.to_dataframe()
        
        # Calculate max clicks and max click rate before formatting
        df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
        df['max_click_rate'] = df['click_rate'].apply(
            lambda x: f"{max(x) * 100:.2f}%" if x and max(x) > 0 else "0.00%"
        )
        
        # Format click rates as percentages
        df['click_rate'] = df['click_rate'].apply(
            lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
        )
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        safe_name = sanitize_filename(set_name)
        response['Content-Disposition'] = f'attachment; filename="{safe_name}.csv"'
        
        df.to_csv(response, index=False)
        
        return response
        
    except ContentSet.DoesNotExist:
        messages.error(request, f"Content set '{set_name}' not found.")
        return redirect('analytics:insights')
    except Exception as e:
        messages.error(request, f"Error downloading CSV: {str(e)}")
        return redirect('analytics:insights')


@login_required
@require_POST
@require_valid_api_credentials
def refresh_posts(request):
    """
    Refresh posts data from Beehiiv API and update the database.
    """
    from .models import Publication

    try:
        # Get API credentials (already validated by decorator)
        beehiiv_token, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        # Get or create the Publication record
        usage = UsageAccount.objects.get(user=request.user)
        pub_data = next((p for p in usage.available_publications if p["id"] == beehiiv_pub_id), None)
        publication, _ = Publication.objects.get_or_create(
            pub_id=beehiiv_pub_id,
            defaults={
                'name': pub_data.get('name', 'Unknown') if pub_data else 'Unknown',
                'organization_name': pub_data.get('organization_name', '') if pub_data else ''
            }
        )

        # Fetch and process posts data
        posts_df, result_message = async_to_sync(refresh_posts_data)(beehiiv_token, beehiiv_pub_id)

        if posts_df is None:
            messages.error(request, result_message)
            return redirect('analytics:posts')

        # Update database with new posts
        created_count = 0
        updated_count = 0

        for _, row in posts_df.iterrows():
            # Handle null publish_date for drafts
            publish_date = row['publish_date']
            if pd.isna(publish_date):
                publish_date = None

            post, created = Post.objects.update_or_create(
                post_id=row['id'],
                user=request.user,
                defaults={
                    'publication': publication,
                    'title': row['title'],
                    'subtitle': row.get('subtitle', ''),
                    'status': row.get('status', 'Published'),
                    'creation_date': row.get('creation_date'),
                    'publish_date': publish_date,
                    'recipients': row.get('recipients', 0),
                    'delivered': row.get('delivered', 0),
                    'email_opens': row.get('email_opens', 0),
                    'unique_email_opens': row.get('unique_email_opens', 0),
                    'email_clicks': row.get('email_clicks', 0),
                    'unique_email_clicks': row.get('unique_email_clicks', 0),
                    'unsubscribes': row.get('unsubscribes', 0),
                    'spam_reports': row.get('spam_reports', 0),
                }
            )
            
            if created:
                created_count += 1
            else:
                updated_count += 1
        
        messages.success(request, "Updated posts")
        
    except Exception as e:
        messages.error(request, f"Error refreshing posts: {str(e)}")
    
    return redirect('analytics:posts')


@login_required
@require_POST
@require_valid_api_credentials
def download_click_visualization(request):
    """
    Generate and download click visualization HTML files for selected posts.
    Returns a ZIP file containing HTML files with click counts overlaid.
    """
    import zipfile
    import io
    from .utils import generate_click_visualization_html

    try:
        # Get API credentials (already validated by decorator)
        beehiiv_token, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        # Get selected post indices
        selected_indices = request.POST.getlist('selected_posts')

        if not selected_indices:
            messages.error(request, "Please select at least one post.")
            return redirect('analytics:posts')

        # Convert indices to integers
        selected_indices = [int(idx) for idx in selected_indices]

        # Load posts from database filtered by publication and user
        posts_df = load_posts_from_db(publication_id=beehiiv_pub_id, user=request.user)
        posts_df = posts_df.iloc[::-1].reset_index(drop=True)  # Reverse to match display

        # Get selected posts
        posts_of_interest = posts_df.iloc[selected_indices]

        # Extract post IDs
        post_ids = posts_of_interest['id'].tolist()

        # Fetch HTMLs and clicks from API
        htmls, clicks_by_id = async_to_sync(fetch_posts_html_and_clicks_parallel)(post_ids, beehiiv_token, beehiiv_pub_id)
        
        if not htmls:
            messages.error(request, "Failed to fetch content from Beehiiv.")
            return redirect('analytics:posts')
        
        # Build list of generated files
        generated_files = []

        for _, row in posts_of_interest.iterrows():
            post_id = row['id']
            html_filename = f"{post_id}.html"

            # Check if we have HTML and clicks for this post
            if html_filename not in htmls:
                continue

            if post_id not in clicks_by_id:
                continue

            # Get the HTML and clicks
            post_html = htmls[html_filename]
            clicks_dict = clicks_by_id[post_id]
            unique_email_opens = row['unique_email_opens']

            # Generate click visualization HTML
            visualization_html = generate_click_visualization_html(
                post_html,
                clicks_dict,
                unique_email_opens
            )

            # Create a safe filename from the post title
            safe_title = sanitize_filename(row['title'])[:50]

            generated_files.append((f"{safe_title}.html", visualization_html))

        if not generated_files:
            messages.error(request, "Failed to generate any visualizations.")
            return redirect('analytics:posts')

        # Single file: return HTML directly
        if len(generated_files) == 1:
            filename, html_content = generated_files[0]
            response = HttpResponse(html_content, content_type='text/html')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        # Multiple files: create ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for filename, html_content in generated_files:
                zip_file.writestr(filename, html_content)

        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="click_visualizations.zip"'

        return response
        
    except Exception as e:
        messages.error(request, f"Error generating click visualizations: {str(e)}")
        return redirect('analytics:posts')


@login_required
@require_POST
@require_valid_api_credentials
def download_annotated_posts(request):
    """
    Generate and download annotated HTML files for selected posts using selected reports.
    Returns a ZIP file containing annotated HTML files with tips inserted.
    """
    import zipfile
    import io

    try:
        # Get API credentials (already validated by decorator)
        beehiiv_token, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        # Get selected post indices
        selected_indices = request.POST.getlist('selected_posts')
        # Get selected report IDs
        selected_report_ids = request.POST.getlist('selected_reports')

        if not selected_indices:
            messages.error(request, "Please select at least one post.")
            return redirect('analytics:posts')

        if not selected_report_ids:
            messages.error(request, "Please select at least one report.")
            return redirect('analytics:posts')

        # Convert indices to integers
        selected_indices = [int(idx) for idx in selected_indices]
        selected_report_ids = [int(rid) for rid in selected_report_ids]

        # Load posts from database filtered by publication and user
        posts_df = load_posts_from_db(publication_id=beehiiv_pub_id, user=request.user)
        posts_df = posts_df.iloc[::-1].reset_index(drop=True)  # Reverse to match display

        # Get selected posts
        posts_of_interest = posts_df.iloc[selected_indices]

        # Get report texts for selected reports (owned by this user)
        reports = Report.objects.filter(id__in=selected_report_ids, content_set__user=request.user)
        content_perf_evals = [report.report_text for report in reports]

        if not content_perf_evals:
            messages.error(request, "No valid reports found.")
            return redirect('analytics:posts')

        # Get post IDs (beehiiv post_id, not Django id)
        post_ids = posts_of_interest['id'].tolist()

        # Charge credits before annotation (1 credit per post)
        credits_needed = len(post_ids) * settings.CREDITS_PER_ANNOTATION
        charge_credits(request.user, credits_needed)

        # Run parallel annotation
        annotated_htmls = async_to_sync(annotate_posts_parallel)(post_ids, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=request.user)

        if not annotated_htmls:
            messages.error(request, "Failed to generate any annotated posts.")
            return redirect('analytics:posts')

        # Build list of generated files
        generated_files = []

        for _, row in posts_of_interest.iterrows():
            post_id = row['id']

            if post_id not in annotated_htmls:
                continue

            annotated_html = annotated_htmls[post_id]

            # Create a safe filename from the post title
            safe_title = sanitize_filename(row['title'])[:50]

            generated_files.append((f"{safe_title}.html", annotated_html))

        # Single file: return HTML directly
        if len(generated_files) == 1:
            filename, html_content = generated_files[0]
            response = HttpResponse(html_content, content_type='text/html')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'

            # Add credit info headers for frontend to update sidebar
            from .models import UsageAccount
            try:
                usage = UsageAccount.objects.get(user=request.user)
                usage.ensure_current_period()
                response['X-Credits-Used'] = str(usage.used_this_period)
                response['X-Credits-Quota'] = str(usage.monthly_quota)
                response['Access-Control-Expose-Headers'] = 'X-Credits-Used, X-Credits-Quota'
            except UsageAccount.DoesNotExist:
                pass

            return response

        # Multiple files: create ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for filename, html_content in generated_files:
                zip_file.writestr(filename, html_content)

        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="annotated_posts.zip"'

        # Add credit info headers for frontend to update sidebar
        from .models import UsageAccount
        try:
            usage = UsageAccount.objects.get(user=request.user)
            usage.ensure_current_period()
            response['X-Credits-Used'] = str(usage.used_this_period)
            response['X-Credits-Quota'] = str(usage.monthly_quota)
            response['Access-Control-Expose-Headers'] = 'X-Credits-Used, X-Credits-Quota'
        except UsageAccount.DoesNotExist:
            pass

        return response

    except NotEnoughCredits as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=402)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f"Error generating annotated posts: {str(e)}"
        }, status=500)


@login_required
@require_POST
def save_report(request):
    """
    Save a generated report to the database.
    """
    try:
        report_name = request.POST.get('report_name', '').strip()
        report_text = request.POST.get('report_text', '').strip()
        set_name = request.POST.get('set_name', '').strip()
        
        if not report_name:
            return JsonResponse({
                'success': False,
                'error': 'Report name is required.'
            }, status=400)
        
        if not report_text:
            return JsonResponse({
                'success': False,
                'error': 'Report text is required.'
            }, status=400)
        
        if not set_name:
            return JsonResponse({
                'success': False,
                'error': 'Content set name is required.'
            }, status=400)
        
        # Get the content set (must be owned by this user)
        content_set = ContentSet.objects.get(name=set_name, user=request.user)

        # Check if a report with this name already exists for this content set
        if Report.objects.filter(name=report_name, content_set=content_set).exists():
            return JsonResponse({
                'success': False,
                'error': f'A report named "{report_name}" already exists for this content set.'
            }, status=400)
        
        # Create the report
        report = Report.objects.create(
            name=report_name,
            content_set=content_set,
            report_text=report_text
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Report "{report_name}" saved successfully!',
            'report_id': report.id
        })
        
    except ContentSet.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Content set '{set_name}' not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def load_report(request, report_id):
    """
    Load a saved report and return as JSON.
    """
    try:
        report = Report.objects.get(id=report_id, content_set__user=request.user)
        
        return JsonResponse({
            'success': True,
            'report_name': report.name,
            'report_text': report.report_text,
            'content_set_name': report.content_set.name,
            'created_at': report.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })
        
    except Report.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f"Report not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def get_all_reports(request):
    """
    Get all reports for the current publication's content sets.
    """
    from .models import Publication

    try:
        # Get current publication for filtering
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        # Filter reports by publication and user through content_set
        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
            reports = Report.objects.filter(
                content_set__publication=publication,
                content_set__user=request.user
            ).order_by('-created_at')
        except Publication.DoesNotExist:
            reports = Report.objects.none()

        reports_data = [
            {
                'id': report.id,
                'name': report.name,
                'content_set_name': report.content_set.name,
                'created_at': report.created_at.strftime('created %b %-d, %Y')
            }
            for report in reports
        ]

        return JsonResponse({
            'success': True,
            'reports': reports_data
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def delete_report(request, report_id):
    """
    Delete a saved report.
    """
    try:
        report = Report.objects.get(id=report_id, content_set__user=request.user)
        report_name = report.name
        report.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Report "{report_name}" deleted successfully.'
        })
        
    except Report.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Report not found.'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
