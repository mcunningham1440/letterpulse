"""
Views for the analytics app.
"""

from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib import messages
from django.conf import settings
import pandas as pd
import json
import asyncio
import ast
from asgiref.sync import async_to_sync

from .models import Post, ContentSet
from .utils import (
    load_posts_from_db,
    fetch_posts_html_and_clicks_parallel,
    extract_items_parallel,
    generate_content_insights,
    refresh_posts_data
)


def index(request):
    """Redirect to extract page"""
    return redirect('analytics:extract')


def extract_view(request):
    """
    Display the extract page with posts table.
    """
    # Load posts from database
    posts_df = load_posts_from_db()
    
    # Reverse order so newer posts appear first
    posts_df = posts_df.iloc[::-1].reset_index(drop=True)
    
    # Convert to list of dicts for template
    posts_data = posts_df.to_dict('records')
    
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
        
        # Format click rates as percentages
        df['click_rate'] = df['click_rate'].apply(
            lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
        )
        
        # Calculate max clicks
        df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
        
        extracted_items_data = df.to_dict('records')
    
    # Get all content sets for the dropdown
    all_content_sets = ContentSet.objects.all().order_by('name')
    
    context = {
        'posts': posts_data,
        'extracted_items': extracted_items_data,
        'all_content_sets': all_content_sets,
    }
    
    return render(request, 'analytics/extract.html', context)


@require_POST
def run_extraction(request):
    """
    Run content extraction on selected posts.
    """
    try:
        # Get form data
        selected_indices = request.POST.getlist('selected_posts')
        content_desc = request.POST.get('content_desc', '').strip()
        
        if not content_desc:
            messages.error(request, "Please provide a content description.")
            return redirect('analytics:extract')
        
        if not selected_indices:
            messages.error(request, "Please select at least one post.")
            return redirect('analytics:extract')
        
        # Convert indices to integers
        selected_indices = [int(idx) for idx in selected_indices]
        
        # Load data from database
        posts_df = load_posts_from_db()
        posts_df = posts_df.iloc[::-1].reset_index(drop=True)  # Reverse to match display
        
        # Get selected posts
        posts_of_interest = posts_df.iloc[selected_indices]
        
        # Extract post IDs for fetching HTMLs and clicks
        post_ids = posts_of_interest['id'].tolist()
        
        # Fetch HTMLs and clicks dynamically from API in parallel
        messages.info(request, f"Fetching HTML content and click data for {len(post_ids)} posts...")
        htmls, clicks_by_id = async_to_sync(fetch_posts_html_and_clicks_parallel)(post_ids)
        
        if not htmls:
            messages.error(request, "Failed to fetch HTML content from API.")
            return redirect('analytics:extract')
        
        # Prepare data for parallel extraction (now includes post_id)
        posts_data = [
            (row.id, row.title, f"{row.id}.html", row.publish_date_cst, row.unique_email_opens)
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
        
        # Run async extraction
        items_list = async_to_sync(extract_items_parallel)(
            posts_data, 
            content_desc, 
            htmls, 
            clicks_by_id
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
        
    except Exception as e:
        messages.error(request, f"Error during extraction: {str(e)}")
    
    return redirect('analytics:extract')


@require_POST
def delete_items(request):
    """
    Delete selected items from extracted items.
    """
    try:
        items_to_delete = request.POST.getlist('items_to_delete')
        
        if not items_to_delete:
            messages.warning(request, "No items selected for deletion.")
            return redirect('analytics:extract')
        
        # Convert to integers
        items_to_delete = [int(idx) for idx in items_to_delete]
        
        # Get current extracted items
        extracted_items = request.session.get('extracted_items', [])
        
        if not extracted_items:
            messages.error(request, "No extracted items found.")
            return redirect('analytics:extract')
        
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
    
    return redirect('analytics:extract')


@require_POST
def save_content_set(request):
    """
    Save extracted items as a named content set or add to existing set.
    """
    try:
        set_mode = request.POST.get('set_mode', 'create')
        
        # Get extracted items from session
        extracted_items = request.session.get('extracted_items', [])
        
        if not extracted_items:
            messages.error(request, "No extracted items to save.")
            return redirect('analytics:extract')
        
        if set_mode == 'create':
            # Create new content set
            content_set_name = request.POST.get('content_set_name', '').strip()
            
            if not content_set_name:
                messages.error(request, "Please provide a name for the content set.")
                return redirect('analytics:extract')
            
            # Check if name already exists
            if ContentSet.objects.filter(name=content_set_name).exists():
                messages.error(request, f"A content set named '{content_set_name}' already exists. Please choose a different name.")
                return redirect('analytics:extract')
            
            # Create ContentSet
            df = pd.DataFrame(extracted_items)
            content_set = ContentSet.from_dataframe(content_set_name, df)
            content_set.save()
            
            messages.success(request, f"Content set '{content_set_name}' saved successfully!")
            
        elif set_mode == 'add':
            # Add to existing content set
            existing_set_name = request.POST.get('existing_set_name', '').strip()
            keep_copy = request.POST.get('keep_copy') == 'true'
            
            if not existing_set_name:
                messages.error(request, "Please select an existing content set.")
                return redirect('analytics:extract')
            
            # Get the existing content set
            try:
                existing_set = ContentSet.objects.get(name=existing_set_name)
            except ContentSet.DoesNotExist:
                messages.error(request, f"Content set '{existing_set_name}' not found.")
                return redirect('analytics:extract')
            
            # Keep a copy of the old set if requested
            if keep_copy:
                import datetime
                backup_name = f"{existing_set_name} copy"
                
                # Create a backup copy
                backup_set = ContentSet(
                    name=backup_name,
                    description=f"Backup of '{existing_set_name}' before adding items",
                    items_data=existing_set.items_data
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
    
    return redirect('analytics:extract')


def analyze_view(request):
    """
    Display the analyze page with content sets.
    """
    # Get all available content sets
    content_sets = ContentSet.objects.all().order_by('-created_at')
    
    context = {
        'content_sets': content_sets,
    }
    
    return render(request, 'analytics/analyze.html', context)


def load_content_set(request, set_name):
    """
    Load a specific content set and return as JSON.
    """
    try:
        content_set = ContentSet.objects.get(name=set_name)
        df = content_set.to_dataframe()
        
        # Format for display
        if not df.empty:
            # Format click rates as percentages
            df['click_rate'] = df['click_rate'].apply(
                lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
            )
            
            # Calculate max clicks
            df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
            
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
        
        content_set = ContentSet.objects.get(name=set_name)
        df = content_set.to_dataframe()
        
        if df.empty:
            return JsonResponse({
                'success': False,
                'error': 'Content set is empty.'
            }, status=400)
        
        # Generate insights using async function
        response = async_to_sync(generate_content_insights)(df)
        insights = response.choices[0].message.content
        
        return JsonResponse({
            'success': True,
            'insights': insights,
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


def download_csv(request, set_name):
    """
    Download a content set as CSV.
    """
    try:
        content_set = ContentSet.objects.get(name=set_name)
        df = content_set.to_dataframe()
        
        # Format click rates as percentages
        df['click_rate'] = df['click_rate'].apply(
            lambda x: [f"{rate * 100:.2f}%" for rate in x] if x else []
        )
        
        # Calculate max clicks
        df['max_clicks'] = df['clicks'].apply(lambda x: max(x) if x else 0)
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{set_name}.csv"'
        
        df.to_csv(response, index=False)
        
        return response
        
    except ContentSet.DoesNotExist:
        messages.error(request, f"Content set '{set_name}' not found.")
        return redirect('analytics:analyze')
    except Exception as e:
        messages.error(request, f"Error downloading CSV: {str(e)}")
        return redirect('analytics:analyze')


@require_POST
def refresh_posts(request):
    """
    Refresh posts data from Beehiiv API and update the database.
    """
    try:
        messages.info(request, "Fetching latest posts from Beehiiv API...")
        
        # Fetch and process posts data
        posts_df, result_message = async_to_sync(refresh_posts_data)()
        
        if posts_df is None:
            messages.error(request, result_message)
            return redirect('analytics:extract')
        
        # Update database with new posts
        created_count = 0
        updated_count = 0
        
        for _, row in posts_df.iterrows():
            post, created = Post.objects.update_or_create(
                post_id=row['id'],
                defaults={
                    'title': row['title'],
                    'subtitle': row.get('subtitle', ''),
                    'publish_date_cst': row['publish_date_cst'],
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
        
        messages.success(
            request, 
            f"Successfully refreshed posts! Created {created_count} new posts and updated {updated_count} existing posts."
        )
        
    except Exception as e:
        messages.error(request, f"Error refreshing posts: {str(e)}")
    
    return redirect('analytics:extract')


@require_POST
def download_click_visualization(request):
    """
    Generate and download click visualization HTML files for selected posts.
    Returns a ZIP file containing HTML files with click counts overlaid.
    """
    import zipfile
    import io
    from .utils import generate_click_visualization_html
    
    try:
        # Get selected post indices
        selected_indices = request.POST.getlist('selected_posts')
        
        if not selected_indices:
            messages.error(request, "Please select at least one post.")
            return redirect('analytics:extract')
        
        # Convert indices to integers
        selected_indices = [int(idx) for idx in selected_indices]
        
        # Load posts from database
        posts_df = load_posts_from_db()
        posts_df = posts_df.iloc[::-1].reset_index(drop=True)  # Reverse to match display
        
        # Get selected posts
        posts_of_interest = posts_df.iloc[selected_indices]
        
        # Extract post IDs
        post_ids = posts_of_interest['id'].tolist()
        
        # Fetch HTMLs and clicks from API
        messages.info(request, f"Fetching HTML content and click data for {len(post_ids)} posts...")
        htmls, clicks_by_id = async_to_sync(fetch_posts_html_and_clicks_parallel)(post_ids)
        
        if not htmls:
            messages.error(request, "Failed to fetch HTML content from API.")
            return redirect('analytics:extract')
        
        # Create in-memory ZIP file
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
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
                safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in row['title'])
                safe_title = safe_title[:50]  # Limit length
                
                # Add to ZIP with a descriptive filename
                zip_filename = f"{safe_title}_{post_id}_clicks.html"
                zip_file.writestr(zip_filename, visualization_html)
        
        # Prepare the response
        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="click_visualizations.zip"'
        
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating click visualizations: {str(e)}")
        return redirect('analytics:extract')
