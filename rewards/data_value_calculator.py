import datetime as dt
from typing import Optional
from common.data import DataLabel, DataSource, TimeBucket
from common.data_v2 import ScorableDataEntityBucket
from rewards.data import DataDesirabilityLookup

from rewards import data_desirability_lookup


class DataValueCalculator:
    """Calculates how rewards are distributed across DataSources and DataLabels."""

    def __init__(self, model: DataDesirabilityLookup = data_desirability_lookup.LOOKUP):
        self.model = model

    def get_score_for_data_entity_bucket(
        self, scorable_data_entity_bucket: ScorableDataEntityBucket
    ) -> float:
        """Returns the score for the given data entity bucket.

        A data entity bucket is scored as follows:
            1. It is weighted based on the weight of its data source.
            2. It's scaled based on the Label. This may be negative if the data is undesirable.
            3. It's scaled based on the age of the data, where newer data is considered more valuable.
        """

        label = (
            DataLabel(value=scorable_data_entity_bucket.label)
            if scorable_data_entity_bucket.label
            else None
        )
        data_type_scale_factor = self._scale_factor_for_source_and_label(
            scorable_data_entity_bucket.source, label
        )
        time_scalar = self._scale_factor_for_age(
            TimeBucket(id=scorable_data_entity_bucket.time_bucket_id)
        )
        return (
            data_type_scale_factor
            * time_scalar
            * scorable_data_entity_bucket.scorable_bytes
        )

    def _scale_factor_for_source_and_label(
        self, data_source: DataSource, label: Optional[DataLabel]
    ) -> float:
        """Returns the score scalar for the given data source and label."""
        data_source_reward = self.model.distribution[data_source]
        label_factor = data_source_reward.label_scale_factors.get(
            label, data_source_reward.default_scale_factor
        )
        return data_source_reward.weight * label_factor

    def _scale_factor_for_age(self, time_bucket: TimeBucket) -> float:
        """Returns the score scalar for data ."""
        # Data age is scored using a linear depreciation function, where data from now is scored 1 and data
        # that is max_age_in_hours old is scored 0.5.
        # All data older than max_age_in_hours is scored 0.
        data_age_in_hours = (
            dt.datetime.now(tz=dt.timezone.utc)
            - TimeBucket.to_date_range(time_bucket).start
        ).total_seconds() // 3600

        # Safe guard against future data.
        data_age_in_hours = max(0, data_age_in_hours)

        if data_age_in_hours > self.model.max_age_in_hours:
            return 0.0
        return 1.0 - (data_age_in_hours / (2 * self.model.max_age_in_hours))
