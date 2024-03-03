import datetime as dt
import unittest
from common import constants

from sympy import timed
from scraping.x.model import XContent


class TestModel(unittest.TestCase):
    def test_equality(self):
        """Tests validation of equivalent XContent instances."""
        timestamp = dt.datetime.now()
        # Create two XContent instances with the same values
        xcontent1 = XContent(
            username="user1",
            text="Hello world",
            url="https://twitter.com/123",
            timestamp=timestamp,
            tweet_hashtags=["#bittensor", "$TAO"],
        )
        xcontent2 = XContent(
            username="user1",
            text="Hello world",
            url="https://twitter.com/123",
            timestamp=timestamp,
            tweet_hashtags=["#bittensor", "$TAO"],
        )

        # Check if the two instances are equivalent
        self.assertTrue(xcontent1 == xcontent2)
        self.assertTrue(xcontent2 == xcontent1)

    def test_equality_not_equivalent(self):
        """Tests validation of non-equivalent XContent instances."""
        timestamp = dt.datetime.now()
        content = XContent(
            username="user1",
            text="Hello world",
            url="https://twitter.com/123",
            timestamp=timestamp,
            tweet_hashtags=["#bittensor", "$TAO"],
        )

        non_matching_content = [
            content.copy(update={"username": "user2"}),
            content.copy(update={"text": "Hello world!"}),
            content.copy(update={"url": "https://twitter.com/456"}),
            content.copy(update={"timestamp": timestamp + dt.timedelta(seconds=1)}),
            # Hashtag ordering needs to be deterministic. Verify changing the order of the hashtags makes the content non-equivalent.
            content.copy(update={"tweet_hashtags": ["#TAO", "#bittensor"]}),
        ]

        for c in non_matching_content:
            self.assertFalse(content == c)
            self.assertFalse(c == content)

    def test_label_truncation(self):
        """Tests that XContents correctly truncate labels to 32 characters when converting to DataEntities"""
        timestamp = dt.datetime.now(tz=dt.timezone.utc)
        content = XContent(
            username="user1",
            text="Hello world",
            url="https://twitter.com/123",
            timestamp=timestamp,
            tweet_hashtags=["#loooooooooooooooooooooooonghashtag", "$TAO"],
        )
        entity = XContent.to_data_entity(content=content, obfuscate_content_date=False)

        self.assertEqual(len(entity.label.value), constants.MAX_LABEL_LENGTH)
        self.assertEqual(entity.label.value, "#loooooooooooooooooooooooonghash")

    def test_to_data_entity_non_obfuscated(self):
        timestamp = dt.datetime(
            year=2024,
            month=1,
            day=2,
            hour=3,
            minute=4,
            second=5,
            microsecond=6,
            tzinfo=dt.timezone.utc,
        )
        content = XContent(
            username="user1",
            text="Hello world",
            url="https://twitter.com/123",
            timestamp=timestamp,
            tweet_hashtags=["#bittensor", "$TAO"],
        )

        # Convert to entity and back to check granularity of the content timestamp.
        entity = XContent.to_data_entity(content=content, obfuscate_content_date=False)
        content_roundtrip = XContent.from_data_entity(entity)

        # Both the entity datetime and the entity content datetime should have full granularity.
        self.assertEqual(entity.datetime, timestamp)
        self.assertEqual(content_roundtrip.timestamp, timestamp)

    def test_to_data_entity_obfuscated(self):
        timestamp = dt.datetime(
            year=2024,
            month=3,
            day=1,
            hour=1,
            minute=1,
            second=1,
            microsecond=1,
            tzinfo=dt.timezone.utc,
        )
        content = XContent(
            username="user1",
            text="Hello world",
            url="https://twitter.com/123",
            timestamp=timestamp,
            tweet_hashtags=["#bittensor", "$TAO"],
        )

        # Convert to entity and back to check granularity of the content timestamp.
        entity = XContent.to_data_entity(content=content, obfuscate_content_date=True)
        content_roundtrip = XContent.from_data_entity(entity)

        # The entity datetime should have full granularity but the roundtripped content should not.
        self.assertEqual(entity.datetime, timestamp)
        self.assertEqual(
            content_roundtrip.timestamp,
            dt.datetime(
                year=2024,
                month=3,
                day=1,
                hour=1,
                minute=1,
                second=0,
                microsecond=0,
                tzinfo=dt.timezone.utc,
            ),
        )


if __name__ == "__main__":
    unittest.main()
