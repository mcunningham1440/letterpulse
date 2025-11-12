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
    load_htmls, 
    load_clicks_by_title, 
    load_posts_csv,
    extract_items_parallel,
    generate_content_insights
)


def index(request):
    """Redirect to extract page"""
    return redirect('analytics:extract')


def extract_view(request):
    """
    Display the extract page with posts table.
    """
    # Load posts from CSV (in production, you'd load from DB)
    posts_df = load_posts_csv()
    
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
    
    context = {
        'posts': posts_data,
        'extracted_items': extracted_items_data,
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
        
        # Load data
        posts_df = load_posts_csv()
        posts_df = posts_df.iloc[::-1].reset_index(drop=True)  # Reverse to match display
        htmls = load_htmls()
        clicks_by_title = load_clicks_by_title()
        
        # Get selected posts
        posts_of_interest = posts_df.iloc[selected_indices]
        
        # Prepare data for parallel extraction
        posts_data = [
            (row.title, f"{row.id}.html", row.publish_date_cst, row.unique_email_opens)
            for _, row in posts_of_interest.iterrows()
        ]
        
        # Check for missing HTML files
        missing_files = [
            title for title, html_file, _, _ in posts_data 
            if html_file not in htmls
        ]
        if missing_files:
            for title in missing_files:
                messages.warning(request, f"HTML file not found for post: {title}")
        
        # Run async extraction
        items_list = async_to_sync(extract_items_parallel)(
            posts_data, 
            content_desc, 
            htmls, 
            clicks_by_title
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
    Save extracted items as a named content set.
    """
    try:
        content_set_name = request.POST.get('content_set_name', '').strip()
        
        if not content_set_name:
            messages.error(request, "Please provide a name for the content set.")
            return redirect('analytics:extract')
        
        # Check if name already exists
        if ContentSet.objects.filter(name=content_set_name).exists():
            messages.error(request, f"A content set named '{content_set_name}' already exists. Please choose a different name.")
            return redirect('analytics:extract')
        
        # Get extracted items from session
        extracted_items = request.session.get('extracted_items', [])
        
        if not extracted_items:
            messages.error(request, "No extracted items to save.")
            return redirect('analytics:extract')
        
        # Create ContentSet
        df = pd.DataFrame(extracted_items)
        content_set = ContentSet.from_dataframe(content_set_name, df)
        content_set.save()
        
        messages.success(request, f"Content set '{content_set_name}' saved successfully!")
        
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
