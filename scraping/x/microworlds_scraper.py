# import sys
# sys.path.append("/mnt/d/Google Drive/Temp/Desktop/Freelance/Clients/Potiential Clients/Varun S/data-universe")
import asyncio
import json
import threading
import traceback
import bittensor as bt
from typing import List
from common import constants
from common.data import DataEntity, DataLabel, DataSource
from common.date_range import DateRange
from scraping.scraper import ScrapeConfig, Scraper, ValidationResult
from scraping.apify import ActorRunner, RunConfig
from scraping.x.model import XContent
from scraping.x import utils
from scraping.tweet_scraper import TwitterScraper_V1
import datetime as dt
import nest_asyncio
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
nest_asyncio.apply()

class MicroworldsTwitterScraper(Scraper):
    """
    Scrapes tweets using the Microworlds Twitter Scraper: https://console.apify.com/actors/heLL6fUofdPgRXZie.
    """

    ACTOR_ID = "heLL6fUofdPgRXZie"

    SCRAPE_TIMEOUT_SECS = 120

    BASE_RUN_INPUT = {
        "maxRequestRetries": 5,
        "searchMode": "live",
    }

    # As of 2/5/24 this actor only takes 256 MB in the default config so we can run a full batch without hitting shared actor memory limits.
    concurrent_validates_semaphore = threading.BoundedSemaphore(20)

    def __init__(self, runner: ActorRunner = ActorRunner()):
        self.runner = runner

    async def validate(self, entities: List[DataEntity]) -> List[ValidationResult]:
        """Validate the correctness of a DataEntity by URI."""

        async def validate_entity(entity) -> ValidationResult:
            if not utils.is_valid_twitter_url(entity.uri):
                return ValidationResult(
                    is_valid=False,
                    reason="Invalid URI.",
                    content_size_bytes_validated=entity.content_size_bytes,
                )

            attempt = 0
            max_attempts = 2
            while attempt < max_attempts:
                # Increment attempt.
                attempt += 1

                # On attempt 1 we fetch the exact number of tweets. On retry we fetch more in case they are in replies.
                tweet_count = 1 if attempt == 1 else 5


                # Retrieve the tweets from Apify.
                dataset: List[dict] = None
                try:
                    dataset: List[dict] = TwitterScraper_V1(
                        uri=entity.uri
                    ).tweet()
                except (
                    Exception
                ) as e:  # Catch all exceptions here to ensure we do not exit validation early.
                    if attempt != max_attempts:
                        # Retrying.
                        continue
                    else:
                        bt.logging.error(
                            f"Failed to run actor: {traceback.format_exc()}."
                        )
                        # This is an unfortunate situation. We have no way to distinguish a genuine failure from
                        # one caused by malicious input. In my own testing I was able to make the Actor timeout by
                        # using a bad URI. As such, we have to penalize the miner here. If we didn't they could
                        # pass malicious input for chunks they don't have.
                        return ValidationResult(
                            is_valid=False,
                            reason="Failed to run Actor. This can happen if the URI is invalid, or APIfy is having an issue.",
                            content_size_bytes_validated=entity.content_size_bytes,
                        )

                # Parse the response
                tweets = self._best_effort_parse_dataset_v2(dataset, 'tweet')

                actual_tweet = None
                for tweet in tweets:
                    if tweet.url == entity.uri:
                        actual_tweet = tweet
                        break
                if actual_tweet is None:
                    # Only append a failed result if on final attempt.
                    if attempt == max_attempts:
                        return ValidationResult(
                            is_valid=False,
                            reason="Tweet not found or is invalid.",
                            content_size_bytes_validated=entity.content_size_bytes,
                        )
                else:
                    require_obfuscation = (
                        actual_tweet.timestamp
                        >= constants.REDUCED_CONTENT_DATETIME_GRANULARITY_THRESHOLD
                    )
                    return utils.validate_tweet_content(
                        actual_tweet=actual_tweet,
                        entity=entity,
                        require_obfuscated_content_date=require_obfuscation,
                    )

        if not entities:
            return []

        # Since we are using the threading.semaphore we need to use it in a context outside of asyncio.
        bt.logging.trace("Acquiring semaphore for concurrent microworlds validations.")
        with MicroworldsTwitterScraper.concurrent_validates_semaphore:
            bt.logging.trace(
                "Acquired semaphore for concurrent microworlds validations."
            )
            results = await asyncio.gather(
                *[validate_entity(entity) for entity in entities]
            )

        return results


    async def scrape(self, scrape_config: ScrapeConfig) -> List[DataEntity]:
        """Scrapes a batch of Tweets according to the scrape config."""
        # Construct the query string.
        date_format = "%Y-%m-%d_%H:%M:%S_UTC"
        query = f"since:{scrape_config.date_range.start.astimezone(tz=dt.timezone.utc).strftime(date_format)} until:{scrape_config.date_range.end.astimezone(tz=dt.timezone.utc).strftime(date_format)}"
        if scrape_config.labels:
            label_query = " OR ".join([label.value for label in scrape_config.labels])
            query += f" ({label_query})"
        else:
            # HACK: The search query doesn't work if only a time range is provided.
            # If no label is specified, just search for "e", the most common letter in the English alphabet.
            # I attempted using "#" instead, but that still returned empty results ¯\_(ツ)_/¯
            query += " e"

        # Construct the input to the runner.
        max_items = scrape_config.entity_limit or 150

        bt.logging.trace(f"Performing Twitter scrape for search terms: {query}.")

        # Run the Actor and retrieve the scraped data.
        dataset: List[dict] = None
        try:
            # dataset: List[dict] = search_scrape(query, max_items)
            dataset: List[dict] = TwitterScraper_V1(
                since_date=scrape_config.date_range.start.astimezone(tz=dt.timezone.utc), 
                until_date=scrape_config.date_range.end.astimezone(tz=dt.timezone.utc), 
                limit=max_items, 
                labels=scrape_config.labels).search()
        except Exception:
            bt.logging.error(
                f"Failed to scrape tweets using search terms {query}: {traceback.format_exc()}."
            )
            # TODO: Raise a specific exception, in case the scheduler wants to have some logic for retries.
            return []

        # Return the parsed results, ignoring data that can't be parsed.
        x_contents = self._best_effort_parse_dataset_v2(dataset, 'search')
        bt.logging.success(
            f"Completed scrape for {query}. Scraped {len(x_contents)} items."
        )

        data_entities = []
        for x_content in x_contents:
            if (
                x_content.timestamp
                >= constants.REDUCED_CONTENT_DATETIME_GRANULARITY_THRESHOLD
            ):
                data_entities.append(
                    XContent.to_data_entity(
                        content=x_content, obfuscate_content_date=True
                    )
                )
            else:
                data_entities.append(
                    XContent.to_data_entity(
                        content=x_content, obfuscate_content_date=False
                    )
                )

        return data_entities

    def _best_effort_parse_dataset(self, dataset: List[dict]) -> List[XContent]:
        """Performs a best effort parsing of Apify dataset into List[XContent]

        Any errors are logged and ignored."""
        if dataset == [{"zero_result": True}]:
            return []

        results: List[XContent] = []
        for data in dataset:
            try:
                # Check that we have the required fields.
                if (
                    ("full_text" not in data and "truncated_full_text" not in data)
                    or "url" not in data
                    or "created_at" not in data
                ):
                    continue

                # Truncated_full_text is only populated if "full_text" is truncated.
                text = (
                    data["truncated_full_text"]
                    if "truncated_full_text" in data and data["truncated_full_text"]
                    else data["full_text"]
                )

                # Microworlds returns cashtags separately under symbols.
                # These are returned as list of dicts where the indices key is the first/last index and text is the tag.
                # If there are no hashtags or cashtags they are empty lists.
                hashtags = (
                    data["entities"]["hashtags"]
                    if "entities" in data and "hashtags" in data["entities"]
                    else []
                )
                cashtags = (
                    data["entities"]["symbols"]
                    if "entities" in data and "symbols" in data["entities"]
                    else []
                )

                sorted_tags = sorted(hashtags + cashtags, key=lambda x: x["indices"][0])

                tags = ["#" + item["text"] for item in sorted_tags]

                results.append(
                    XContent(
                        username=utils.extract_user(data["url"]),
                        text=utils.sanitize_scraped_tweet(text),
                        url=data["url"],
                        timestamp=dt.datetime.strptime(
                            data["created_at"], "%a %b %d %H:%M:%S %z %Y"
                        ),
                        tweet_hashtags=tags,
                    )
                )
            except Exception:
                bt.logging.warning(
                    f"Failed to decode XContent from Apify response: {traceback.format_exc()}."
                )

        return results

    def _best_effort_parse_dataset_v2(self, dataset: List[dict], type: str) -> List[XContent]:
        """Performs a best effort parsing of dataset into List[XContent]

        Any errors are logged and ignored."""
        if not dataset:
            return []

        results: List[XContent] = []
        failed = []
        for data in dataset:
            if type == 'search':
                try:
                    if 'tweet' in data['content']['itemContent']['tweet_results']['result']:
                        tweet_data = data['content']['itemContent']['tweet_results']['result']['tweet']['legacy']
                        user_data = data['content']['itemContent']['tweet_results']['result']['tweet']['core']['user_results']['result']['legacy']
                    else:
                        tweet_data = data['content']['itemContent']['tweet_results']['result']['legacy']
                        user_data = data['content']['itemContent']['tweet_results']['result']['core']['user_results']['result']['legacy']
                except Exception as e:
                    tweet_data = {}
                    user_data = {}  
                    # failed.append(data)
                    # bt.logging.error('result' in data['content']['itemContent']['tweet_results']['result'], list(data['content']['itemContent']['tweet_results']['result'].keys()))
                    # bt.logging.error('result' in data['content']['itemContent']['tweet_results']['result']['core']['user_results']['result'], list(data['content']['itemContent']['tweet_results']['result']['core']['user_results']['result'].keys()))
                    bt.logging.error('Error while parsing search tweet data: ', e)
            elif type == 'tweet':
                try:
                    tweet_data = data['data']['tweetResult'][0]['result']['legacy']
                    user_data = data['data']['tweetResult'][0]['result']['core']['user_results']['result']['legacy']
                except Exception as e:
                    tweet_data = {}
                    user_data = {}
                    bt.logging.error('error in tweet_data and user_data', e)
            try:
                # Check that we have the required fields.
                if (
                    ("full_text" not in tweet_data)
                    or ('id_str' not in tweet_data  and 'screen_name' not in user_data)
                    or "created_at" not in tweet_data
                ):
                    continue

                # Truncated_full_text is only populated if "full_text" is truncated.
                text = (
                    tweet_data["full_text"]
                )

                # Microworlds returns cashtags separately under symbols.
                # These are returned as list of dicts where the indices key is the first/last index and text is the tag.
                # If there are no hashtags or cashtags they are empty lists.
                hashtags = (
                    tweet_data["entities"]["hashtags"]
                    if "entities" in tweet_data and "hashtags" in tweet_data["entities"]
                    else []
                )

                cashtags = (
                    tweet_data["entities"]["symbols"]
                    if "entities" in tweet_data and "symbols" in tweet_data["entities"]
                    else []
                )

                sorted_tags = sorted(hashtags + cashtags, key=lambda x: x["indices"][0])

                tags = ["#" + item["text"] for item in sorted_tags]

                results.append(
                    XContent(
                        username=f'@{user_data["screen_name"]}',
                        text=utils.sanitize_scraped_tweet(text),
                        url=f'https://twitter.com/{user_data["screen_name"]}/status/{tweet_data["id_str"]}',
                        timestamp=dt.datetime.strptime(
                            tweet_data["created_at"], "%a %b %d %H:%M:%S %z %Y"
                        ),
                        tweet_hashtags=tags,
                    )
                )
            except Exception:
                bt.logging.warning(
                    f"Failed to decode XContent from Apify response: {traceback.format_exc()}."
                )
        # json.dump(failed, open('failed.json', 'w'))
        return results


async def test_scrape():
    scraper = MicroworldsTwitterScraper()

    entities = await scraper.scrape(
        ScrapeConfig(
            entity_limit=2000,
            date_range=DateRange(
                start=dt.datetime(2024, 1, 30, 0, 0, 0, tzinfo=dt.timezone.utc),
                end=dt.datetime(2024, 3, 2, 9, 0, 0, tzinfo=dt.timezone.utc),
            ),
            labels=[DataLabel(value="#bittensor"), DataLabel(value="#btc")],
        )
    )

    print(f"Scraped {len(entities)} entities: {1}")

    return entities

async def test_validate():
    scraper = MicroworldsTwitterScraper()

    true_entities = [
        DataEntity(
            uri="https://twitter.com/bittensor_alert/status/1748585332935622672",
            datetime=dt.datetime(2024, 1, 20, 5, 56, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=DataLabel(value="#Bittensor"),
            content='{"username":"@bittensor_alert","text":"🚨 #Bittensor Alert: 500 $TAO ($122,655) deposited into #MEXC","url":"https://twitter.com/bittensor_alert/status/1748585332935622672","timestamp":"2024-01-20T5:56:00Z","tweet_hashtags":["#Bittensor", "#TAO", "#MEXC"]}',
            content_size_bytes=318,
        ),
        DataEntity(
            uri="https://twitter.com/HadsonNery/status/1752011223330124021",
            datetime=dt.datetime(2024, 1, 29, 16, 50, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=DataLabel(value="#faleitoleve"),
            content='{"username":"@HadsonNery","text":"Se ele fosse brabo mesmo e eu estaria aqui defendendo ele, pq ele não foi direto no Davi já que a intenção dele era fazer o Davi comprar o barulho dela 🤷🏻\u200d♂️ MC fofoqueiro foi macetado pela CUNHÃ #faleitoleve","url":"https://twitter.com/HadsonNery/status/1752011223330124021","timestamp":"2024-01-29T16:50:00Z","tweet_hashtags":["#faleitoleve"]}',
            content_size_bytes=492,
        ),
        DataEntity(
            uri="https://twitter.com/TcMMTsTc/status/1733441357090545731",
            datetime=dt.datetime(2023, 12, 9, 10, 59, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=None,
            content=b'{"username":"@TcMMTsTc","text":"\xe3\x81\xbc\xe3\x81\x8f\xe7\x9c\xa0\xe3\x81\x84\xe3\x81\xa7\xe3\x81\x99","url":"https://twitter.com/TcMMTsTc/status/1733441357090545731","timestamp":"2023-12-09T10:59:00Z","tweet_hashtags":[]}',
            content_size_bytes=218,
        ),
        DataEntity(
            uri="https://twitter.com/mdniy/status/1743249601925185642",
            datetime=dt.datetime(2024, 1, 5, 12, 34, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=None,
            content='{"username":"@mdniy","text":"🗓January 6, 2024\\n0️⃣8️⃣ Days to Makar Sankranti 2024\\n📍Sun Temple, Surya Pahar, Goalpura, Assam\\n \\nDepartment of Yogic Science and Naturopathy, Mahapurusha Srimanta Sankaradeva Viswavidyalaya, Assam in collaboration with MDNIY is organizing mass Surya Namaskar Demonstration…","url":"https://twitter.com/mdniy/status/1743249601925185642","timestamp":"2024-01-05T12:34:00Z","tweet_hashtags":[]}',
            content_size_bytes=485,
        ),
        DataEntity(
            uri="https://twitter.com/rEQjoewd6WfNFL3/status/1743187684422799519",
            datetime=dt.datetime(2024, 1, 5, 8, 28, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=None,
            content='{"username":"@rEQjoewd6WfNFL3","text":"ありがとうございます\\n\\nそうなんです\\nほんと偶然です\\n聞いたときはビックリしました\\n\\nいえいえ、私の記念日だなんて\\nもったいないです\\n妹の記念日にしてください\\nぷぷっ","url":"https://twitter.com/rEQjoewd6WfNFL3/status/1743187684422799519","timestamp":"2024-01-05T08:28:00Z","tweet_hashtags":[]}',
            content_size_bytes=253,
        ),
        DataEntity(
            uri="https://twitter.com/Sid14290237375/status/1760088426400162274",
            datetime=dt.datetime(2024, 2, 20, 23, 45, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=DataLabel(value="#HowlongcanImakeahashtaganywayIg"),
            content='{"username":"@Sid14290237375","text":"Testing hashtags\\n\\n#HowlongcanImakeahashtaganywayIguessthatthiswillbeagoodtest","url":"https://twitter.com/Sid14290237375/status/1760088426400162274","timestamp":"2024-02-20T23:45:00Z","tweet_hashtags":["#HowlongcanImakeahashtaganywayIguessthatthiswillbeagoodtest"]}',
            content_size_bytes=356,
        ),
    ]

    results = await scraper.validate(entities=true_entities)
    print(f"Validation results: {results}")


async def test_multi_thread_validate():
    scraper = MicroworldsTwitterScraper()

    true_entities = [
        DataEntity(
            uri="https://twitter.com/bittensor_alert/status/1748585332935622672",
            datetime=dt.datetime(2024, 1, 20, 5, 56, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=DataLabel(value="#Bittensor"),
            content='{"username":"@bittensor_alert","text":"🚨 #Bittensor Alert: 500 $TAO ($122,655) deposited into #MEXC","url":"https://twitter.com/bittensor_alert/status/1748585332935622672","timestamp":"2024-01-20T5:56:00Z","tweet_hashtags":["#Bittensor", "#TAO", "#MEXC"]}',
            content_size_bytes=318,
        ),
        DataEntity(
            uri="https://twitter.com/HadsonNery/status/1752011223330124021",
            datetime=dt.datetime(2024, 1, 29, 16, 50, tzinfo=dt.timezone.utc),
            source=DataSource.X,
            label=DataLabel(value="#faleitoleve"),
            content='{"username":"@HadsonNery","text":"Se ele fosse brabo mesmo e eu estaria aqui defendendo ele, pq ele não foi direto no Davi já que a intenção dele era fazer o Davi comprar o barulho dela 🤷🏻\u200d♂️ MC fofoqueiro foi macetado pela CUNHÃ #faleitoleve","url":"https://twitter.com/HadsonNery/status/1752011223330124021","timestamp":"2024-01-29T16:50:00Z","tweet_hashtags":["#faleitoleve"]}',
            content_size_bytes=492,
        ),
    ]

    def sync_validate(entities: list[DataEntity]) -> None:
        """Synchronous version of eval_miner."""
        asyncio.run(scraper.validate(entities))

    threads = [
        threading.Thread(target=sync_validate, args=(true_entities,)) for _ in range(5)
    ]

    for thread in threads:
        thread.start()

    for t in threads:
        t.join(120)


if __name__ == "__main__":
    bt.logging.set_trace(True)
    asyncio.run(test_multi_thread_validate())
    asyncio.run(test_scrape())
    asyncio.run(test_validate())
