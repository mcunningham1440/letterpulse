import requests
import pandas as pd
import os
import asyncio
import aiohttp
import json

from config import config

# Get data on the new posts (parallelized)
pagination_size = 10
header = {"Authorization": config.BEEHIIV_TOKEN}

async def fetch_posts_page(session, page, semaphore):
    url = f"https://api.beehiiv.com/v2/publications/{config.BEEHIIV_PUB_ID}/posts?expand=stats&limit={pagination_size}&page={page}"
    async with semaphore:
        async with session.get(url, headers=header) as response:
            print(f"Page {page} status code: {response.status}")
            data = await response.json()
            return data.get('data', [])

async def fetch_all_posts():
    posts_list = []
    semaphore = asyncio.Semaphore(10)
    async with aiohttp.ClientSession() as session:
        page = 1
        while True:
            tasks = [fetch_posts_page(session, p, semaphore) for p in range(page, page + 5)]
            results = await asyncio.gather(*tasks)
            all_empty = True
            for posts_data in results:
                if posts_data:
                    all_empty = False
                    posts_list.extend(posts_data)
                if len(posts_data) < pagination_size:
                    return posts_list
            if all_empty:
                break
            page += 5
    return posts_list

posts_list = asyncio.run(fetch_all_posts())

posts = {
    'id': [], 
    'title': [], 
    'subtitle': [], 
    'publish_date': [],
    'web_url': [],
    'platform': [],
    'recipients': [],
    'delivered': [],
    'email_opens': [],
    'unique_email_opens': [],
    'email_clicks': [],
    'unique_email_clicks': [],
    'unsubscribes': [],
    'spam_reports': [],
    }
email_keys = {'recipients': 'recipients', 
              'delivered': 'delivered',
              'email_opens': 'opens',
              'unique_email_opens': 'unique_opens', 
              'email_clicks': 'clicks',
              'unique_email_clicks': 'unique_clicks', 
              'unsubscribes': 'unsubscribes', 
              'spam_reports': 'spam_reports'
}
clicks = {}
for post in posts_list:
    for key in posts.keys():
        if key not in email_keys:
            posts[key].append(post[key])
        else:
            posts[key].append(post['stats']['email'][email_keys[key]])

    clicks[post['title']] = post['stats']['clicks']

posts = pd.DataFrame(posts)

posts['publish_date_cst'] = pd.to_datetime(posts['publish_date'], unit='s', utc=True).dt.tz_convert('America/Chicago')
posts['publish_dow'] = posts['publish_date_cst'].dt.strftime('%A')
posts['email_open_rate'] = posts['unique_email_opens'] / posts['delivered']
posts['email_click_rate'] = posts['unique_email_clicks'] / posts['unique_email_opens']

posts = posts.drop(columns=['publish_date'])

clicks_by_title = {}

for title in posts['title']:
    clicks_by_title[title] = {}

    for link_data in clicks[title]:
        url = link_data['url']

        if link_data['email']['unique_clicks'] > 0:
            clicks_by_title[title][url] = max(link_data['email']['unique_clicks'], clicks_by_title[title].get(url, 0))

with open("data/clicks_by_title.json", "w", encoding="utf-8") as f:
    json.dump(clicks_by_title, f, ensure_ascii=False, indent=2)


posts = posts[(posts['recipients'] > 10) & (posts['platform'].isin(('web', 'both'))) & (posts['title'] != "Replit's software engineering agent stuns users")]

posts = posts.drop(columns=['platform'])

posts.to_csv("data/posts.csv", index=False)


for i, row in posts.iterrows():
    id = row['id']
    title = row['title']

    archived_htmls = os.listdir("data/archived_htmls")

    if f"{id}.html" not in archived_htmls:
        url = f"https://api.beehiiv.com/v2/publications/{config.BEEHIIV_PUB_ID}/posts/{id}?expand=free_email_content"

        response = requests.get(url, headers=header)

        with open(f"data/archived_htmls/{id}.html", "w", encoding="utf-8") as f:
            f.write(response.json()['data']['content']['free']['email'])

        print(f"Downloaded {title} from API")

    else:
        print(f"Already have {title}, skipping download")