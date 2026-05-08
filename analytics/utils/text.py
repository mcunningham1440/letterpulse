from bs4 import BeautifulSoup


def html_to_text_with_links(html, max_url_len=50):
    """Convert HTML to plain text, placing truncated link URLs inline after link text."""
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a', href=True):
        link_text = a.get_text(strip=True)
        href = a['href']
        if len(href) > max_url_len:
            href = href[:max_url_len - 3] + "..."
        if link_text:
            a.replace_with(f"{link_text} ({href})")
        else:
            a.replace_with(href)
    return soup.get_text(separator='\n', strip=True)


def truncate_url(url, max_len):
    """Shorten a URL to max_len characters, adding '...' if truncated."""
    if len(url) <= max_len:
        return url
    return url[:max_len - 3] + "..."
