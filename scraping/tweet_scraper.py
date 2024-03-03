import orjson
from twitter import search, scraper
from twitter.search import Search
from twitter.util import find_key
from twitter.constants import *
import requests
import random
import time
from datetime import datetime
from httpx import AsyncClient
from pathlib import Path
import bittensor as bt
import threading

class CredentialManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(CredentialManager, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.credentials = []
        self.credential_locks = {}
        self.access_lock = threading.Condition()
        self._get_and_filter_credentials()

    def _get_and_filter_credentials(self):
        try:
            response = requests.get("http://tstempmail1.pythonanywhere.com/api/credentials/")
            response.raise_for_status()
            fetched_credentials = response.json()
            active_credentials = [cred for cred in fetched_credentials if not self._is_locked(cred)]
            with self.access_lock:
                self.credentials = active_credentials
                self.credential_locks = {cred['id']: threading.Lock() for cred in self.credentials}
                bt.logging.info(f"Fetched {len(self.credentials)} active credentials from database.")
        except requests.RequestException as e:
            bt.logging.error(f"Failed to fetch credentials: {e}")

    def _is_locked(self, credential):
        return self._check_account_status(credential)

    def request_credential(self):
        with self.access_lock:
            while not self.credentials:
                return None
            for credential in self.credentials:
                if self._check_account_status(credential):
                    self.handle_locked_account(credential)
                lock = self.credential_locks[credential['id']]
                if lock.acquire(blocking=False):
                    self.credentials.remove(credential)
                    return credential
            return None

    def handle_locked_account(self, credential):
        with self.access_lock:
            if credential in self.credentials: 
                self.credentials.remove(credential)
                self._update_credential_database(credential, 'locked')
                bt.logging.info(f"Removed and updated locked account: {credential['username']}")


    def release_credential(self, credential):
        with self.access_lock:
            self.credentials.append(credential)
            self.credential_locks[credential['id']].release()
            self.access_lock.notifyAll()

    # Assuming this function checks the real-time status of the account and updates the database if locked
    def _check_account_status(self, credential, retries=3):
        if retries == 0:
            return True
        try:
            results = Search(credential['email'], credential['username'], credential['password'], save=False, debug=0).run(
                limit=1,
                retries=1,
                queries=[
                    {
                        'category': 'Latest',
                        'query': 'covid'
                    }
                ]
            )[0]
        except:
            retries -= 1
            return self._check_account_status(credential, retries)
        if results['account_status']:
            return True
        return False

    def _update_credential_database(self, credential, status):
        try:
            update_url = f"http://tstempmail1.pythonanywhere.com/api/credentials/{credential['id']}/"
            payload = {'status': status}
            response = requests.patch(update_url, json=payload)
            response.raise_for_status()
            bt.logging.info(f"Updated {credential['username']} status to {status}.")
        except requests.RequestException as e:
            bt.logging.error(f"Failed to update credential {credential['id']}: {e}")


class Search(Search):
    def __init__(self, email: str = None, username: str = None, password: str = None, session: search.Client = None, **kwargs):
        self.username = username
        super().__init__(email, username, password, session, **kwargs)
    
    async def paginate(self, client: AsyncClient, query: dict, limit: int, out: Path, **kwargs) -> list[dict]:
        params = {
            'variables': {
                'count': 20,
                'querySource': 'typed_query',
                'rawQuery': query['query'],
                'product': query['category']
            },
            'features': Operation.default_features,
            'fieldToggles': {'withArticleRichContentState': False},
        }

        res = []
        cursor = ''
        total = set()
        while True:
            if cursor:
                params['variables']['cursor'] = cursor
            data, entries, cursor, ratelimit, account_status = await self.backoff(lambda: self.get(client, params), **kwargs)
            res.extend(entries)
            if len(entries) <= 2 or len(total) >= limit or ratelimit or account_status:  # just cursors
                if ratelimit:
                    self.debug and bt.logging.warning(f'[{RED} ({self.username}) RATE LIMIT EXCEEDED. Returned {len(total)} search results for {query["query"]}{RESET}]')
                elif account_status:
                    self.debug and bt.logging.warning(f'[{RED}fail{RESET}] ({self.username}) ACCOUNT LOCKED')
                else:
                    self.debug and bt.logging.debug(
                        f'[{GREEN}success{RESET}]({self.username}) Returned {len(total)} search results for {query["query"]}')
                return {"data": res, "rate_limit": ratelimit, "account_status": account_status}
            total |= set(find_key(entries, 'entryId'))
            self.debug and bt.logging.debug(f'({self.username}) {query["query"]}')
            self.save and (out / f'{time.time_ns()}.json').write_bytes(orjson.dumps(entries))


    async def backoff(self, fn, **kwargs):
        retries = kwargs.get('retries', 3)
        for i in range(retries + 1):
            try:
                data, entries, cursor = await fn()
                if errors := data.get('errors'):
                    for e in errors:
                        if self.debug:
                            bt.logging.warning(f'{YELLOW}({self.username}){e.get("message")}{RESET}')
                        return [], [], '', False, True
                ids = set(find_key(data, 'entryId'))
                if len(ids) >= 2:
                    return data, entries, cursor, False, False
            except Exception as e:
                return None, [], None, True, False

class TwitterScraper_V1:
    def __init__(self, limit=None, since_date=None, until_date=None, labels = None, uri=None):
        self.since_date = since_date
        self.until_date = until_date
        self.labels = labels
        self.uri = uri
        self.limit = limit
        self.since_id = None
        self.used_credentials = []
        self.blocked_credentials = []
        self.total_tweets = self.limit

    def query_generator(self, labels: list, since_date: datetime, until_date: datetime, since_id: str=None):
        date_format = "%Y-%m-%d_%H:%M:%S_UTC"
        query = f"since:{since_date.strftime(date_format)} until:{until_date.strftime(date_format)}"
        if labels:
            label_query = " OR ".join([label.value for label in labels])
            query += f" ({label_query})"
        else:
            query += " e"
        if since_id:
            query = f"since_id:{since_id} " + query
        return query

    def is_ratelimit_execeeded(self, credential):
        results = Search(credential['email'], credential['username'], credential['password'], save=False, debug=0).run(
                limit=1,
                retries=1,
                queries=[
                    {
                        'category': 'Latest',
                        'query': 'covid'
                    }
                ]
        )[0]
        if results['rate_limit']:
            return True
        return False    

    def search(self):
        data = []
        i = 0
        while True:
            credential = CredentialManager().request_credential()
            if not credential:
                i+=1
                t = 2 ** i + random.random()
                bt.logging.warning(f"No available credentials. Sleeping for {t} seconds.")
                time.sleep(t)
                continue
            if self.is_ratelimit_execeeded(credential):
                CredentialManager().release_credential(credential)
                i+=1
                t = 2 ** i + random.random()
                bt.logging.warning(f"Rate limit exceeded for username. Sleeping for {t} seconds.")
                time.sleep(t)
                continue
            i=1
            bt.logging.info(f"Using username {credential['username']}.")
            try:
                sc = Search(credential['email'], credential['username'], credential['password'], save=False, debug=1)
                results = sc.run(
                    limit=self.limit,
                    retries=self.limit,
                    queries=[
                        {
                            'category': 'Latest',
                            'query': self.query_generator(self.labels, self.since_date, self.until_date, self.since_id)
                        }
                    ]
                )[0]
            except Exception as ex:
                bt.logging.error(f"Error occurred while scraping: {ex}")
                CredentialManager().release_credential(credential)
                continue
            CredentialManager().release_credential(credential)
            bt.logging.info(f"Scraped {len(results['data'])} tweets using username {credential['username']}. Total tweets scraped = {len(data)}")
            if results['account_status']:
                if results['data']:
                    self.total_tweets -= len(results['data'])
                    self.limit -= len(results['data'])
                    self.since_date = datetime.strptime(results['data'][-1]['content']['itemContent']['tweet_results']['result']['legacy']["created_at"], "%a %b %d %H:%M:%S %z %Y") #+ timedelta(miliseconds=1)
                    self.since_id = results['data'][-1]['content']['itemContent']['tweet_results']['result']['legacy']["id_str"]
                    data.extend(results['data'])
                bt.logging.warning(f"Account {credential['username']} is locked. Removing from credentials database.")
                CredentialManager().handle_locked_account(credential)
                continue
            elif results['rate_limit']:
                if results['data']:
                    self.total_tweets -= len(results['data'])
                    self.limit -= len(results['data'])
                    self.since_date = datetime.strptime(results['data'][-1]['content']['itemContent']['tweet_results']['result']['legacy']["created_at"], "%a %b %d %H:%M:%S %z %Y") #+ timedelta(miliseconds=1)
                    self.since_id = results['data'][-1]['content']['itemContent']['tweet_results']['result']['legacy']["id_str"]
                    data.extend(results['data'])
                # bt.logging.info(f"Scraped {len(results['data'])} tweets using username {credential['username']}. Total tweets scraped = {len(data)}")
                continue
            else:
                data.extend(results['data'])
                # bt.logging.info(f"Scraped {len(results['data'])} tweets using username {credential['username']}. Total tweets scraped = {len(data)}")
                return data

    def tweet(self):
        credential = random.choice(self.credentials)
        sc = scraper.Scraper(credential['email'], credential['username'], credential['password'], save=False, debug=0)
        tweet_id = self.uri.split('/')[-1]
        result = sc.tweets_by_ids([tweet_id])
        return result

if __name__ == '__main__':
    start = datetime.now()
    print(len(TwitterScraper_V1(labels=['covid'], limit=4000, since_date=datetime(2023, 1, 1), until_date=datetime(2023, 2, 1)).search()))
    print("Time Taken to scrape: ",(datetime.now() - start).seconds/60)
    # # print(len(search_scrape_v2('since:2023-01-01_00:00:00_UTC until:2023-02-01_00:00:00_UTC covid', 1000)))
    # # print(tweet_scrape('https://twitter.com/realDonaldTrump/status/1255230848343981056'))
    # # print(trending_hashtags())
    # asyncio.run(TwitterScraper('covid', 100).search())
    pass

# def search_scrape(query, max_items):
#     # credentials = json.load(open('scraping/x/x_credentials.json', 'r'))
#     global credentials
#     credential = random.choice(credentials['x_credentials'])
#     # print(credential)
#     sc = search.Search(credential['email'], credential['username'], credential['password'], save=False, debug=1)
#     return sc.run(
#         limit=max_items,
#         retries=max_items,
#         queries=[
#             {
#                 'category': 'Latest',
#                 'query': query
#             }
#         ]
#     )[0]

# def tweet_scrape(uri:str):
#     # credentials = json.load(open('scraping/x/x_credentials.json', 'r'))
#     global credentials
#     sc = scraper.Scraper(credentials['x_credentials']['email'], credentials['x_credentials']['username'], credentials['x_credentials']['password'], save=False, debug=0)
#     tweet_id = uri.split('/')[-1]
#     return sc.tweets_by_ids([tweet_id])

# def trending_hashtags():
#     # credentials = json.load(open('scraping/x/x_credentials.json', 'r'))
#     global credentials
#     sc = scraper.Scraper(credentials['x_credentials']['email'], credentials['x_credentials']['username'], credentials['x_credentials']['password'], save=False, debug=1)
#     return sc.trends(['+0530'])
