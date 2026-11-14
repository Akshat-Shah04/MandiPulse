"""
Warehouse for Agmarknet mandi prices, laid out as a star schema.

    DimDate ─┐
    DimMarket ─┼── FactPrice  (one row per market x commodity variety x day)
    DimCommodity ┘

RawRecord keeps every API row exactly as it arrived (a document store inside
Postgres via JSONB), so the cleaned facts can always be rebuilt from source.

Prices are Rs per quintal (100 kg), as published by Agmarknet.
"""

from django.db import models


class DimDate(models.Model):
    """Calendar dimension. Key is YYYYMMDD so facts sort and partition by date."""

    date_key = models.IntegerField(primary_key=True)
    date = models.DateField(unique=True)
    year = models.SmallIntegerField()
    quarter = models.SmallIntegerField()
    month = models.SmallIntegerField()
    iso_week = models.SmallIntegerField()
    day_of_week = models.SmallIntegerField(help_text="0 = Monday")
    is_weekend = models.BooleanField()
    # Indian crop seasons drive prices far more than calendar quarters do.
    crop_season = models.CharField(max_length=8)  # kharif / rabi / zaid

    class Meta:
        ordering = ["date_key"]

    def __str__(self):
        return self.date.isoformat()

    @staticmethod
    def season_for_month(month: int) -> str:
        if month in (6, 7, 8, 9, 10):
            return "kharif"
        if month in (11, 12, 1, 2, 3):
            return "rabi"
        return "zaid"

    @classmethod
    def for_date(cls, d):
        key = d.year * 10000 + d.month * 100 + d.day
        obj, _ = cls.objects.get_or_create(
            date_key=key,
            defaults=dict(
                date=d,
                year=d.year,
                quarter=(d.month - 1) // 3 + 1,
                month=d.month,
                iso_week=d.isocalendar()[1],
                day_of_week=d.weekday(),
                is_weekend=d.weekday() >= 5,
                crop_season=cls.season_for_month(d.month),
            ),
        )
        return obj


class DimMarket(models.Model):
    """A mandi (APMC market). Geography hierarchy: state > district > market."""

    state = models.CharField(max_length=64, db_index=True)
    district = models.CharField(max_length=96, db_index=True)
    market = models.CharField(max_length=128)
    # Filled by the geocoding step (district centroid); null until then.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["state", "district", "market"], name="uniq_market")
        ]

    def __str__(self):
        return f"{self.market}, {self.district}, {self.state}"


class DimCommodity(models.Model):
    commodity = models.CharField(max_length=96, db_index=True)
    variety = models.CharField(max_length=96)
    grade = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["commodity", "variety", "grade"], name="uniq_commodity")
        ]
        verbose_name_plural = "commodities"

    def __str__(self):
        return f"{self.commodity} ({self.variety}, {self.grade})"


class FactPrice(models.Model):
    date = models.ForeignKey(DimDate, on_delete=models.PROTECT, related_name="prices")
    market = models.ForeignKey(DimMarket, on_delete=models.PROTECT, related_name="prices")
    commodity = models.ForeignKey(DimCommodity, on_delete=models.PROTECT, related_name="prices")
    min_price = models.DecimalField(max_digits=10, decimal_places=2)
    max_price = models.DecimalField(max_digits=10, decimal_places=2)
    modal_price = models.DecimalField(max_digits=10, decimal_places=2)
    # Set by the anomaly detector; the map highlights these markets.
    is_anomaly = models.BooleanField(default=False)
    anomaly_score = models.FloatField(null=True, blank=True)
    anomaly_reason = models.CharField(max_length=160, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["date", "market", "commodity"], name="uniq_fact"),
            models.CheckConstraint(
                condition=models.Q(min_price__lte=models.F("max_price")),
                name="min_le_max",
            ),
        ]
        indexes = [models.Index(fields=["commodity", "date"])]

    def __str__(self):
        return f"{self.commodity} @ {self.market} on {self.date}: {self.modal_price}"


class RawRecord(models.Model):
    """Untouched API row (JSONB on Postgres) plus where it came from."""

    payload = models.JSONField()
    fetched_at = models.DateTimeField(auto_now_add=True)
    query = models.JSONField(help_text="Filters used for the API call")
    loaded = models.BooleanField(default=False, help_text="Copied into FactPrice")
    reject_reason = models.CharField(max_length=128, blank=True)


class IngestRun(models.Model):
    """Audit trail for each pull from data.gov.in."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    params = models.JSONField()
    rows_fetched = models.IntegerField(default=0)
    rows_loaded = models.IntegerField(default=0)
    rows_rejected = models.IntegerField(default=0)
    error = models.TextField(blank=True)


# --------------------------------------------------------------------------- aggregates
# Pre-computed roll-ups of FactPrice (an OLAP-style summary layer). The map and
# charts read these instead of re-scanning millions of fact rows per click.
# Rebuilt by prices.aggregates.rebuild() after every ingest.


class AggStateDaily(models.Model):
    date = models.DateField()
    commodity = models.CharField(max_length=96)
    state = models.CharField(max_length=64)
    median_modal = models.FloatField()
    p25_modal = models.FloatField()
    p75_modal = models.FloatField()
    min_price = models.FloatField()
    max_price = models.FloatField()
    markets = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["commodity", "date", "state"], name="uniq_agg_state")
        ]
        indexes = [models.Index(fields=["commodity", "state", "date"])]


class AggNationalDaily(models.Model):
    date = models.DateField()
    commodity = models.CharField(max_length=96)
    median_modal = models.FloatField()
    p25_modal = models.FloatField()
    p75_modal = models.FloatField()
    markets = models.IntegerField()
    states = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["commodity", "date"], name="uniq_agg_national")
        ]


# --------------------------------------------------------------------------- ML outputs


class ModelScore(models.Model):
    """Back-test accuracy of one forecasting model at one horizon."""

    STATE = "state"  # all-India and state median prices
    MANDI = "mandi"  # single mandis (noisier, scored separately)

    trained_at = models.DateTimeField()
    level = models.CharField(max_length=8, default=STATE)
    model = models.CharField(max_length=32)  # naive / seasonal_naive / ridge / random_forest / lstm
    horizon = models.SmallIntegerField(help_text="days ahead")
    mae = models.FloatField(help_text="Rs/quintal")
    mape = models.FloatField(help_text="percent")
    n = models.IntegerField(help_text="forecasts scored")
    is_live = models.BooleanField(default=False, help_text="model chosen for published forecasts")

    class Meta:
        indexes = [models.Index(fields=["trained_at", "model"])]


class Forecast(models.Model):
    """
    Published forecast: one row per series x target day.
    state='' and no market = all India; state only = that state; market set = that one mandi.
    """

    commodity = models.CharField(max_length=96)
    state = models.CharField(max_length=64, blank=True)
    market = models.ForeignKey(DimMarket, null=True, blank=True, on_delete=models.CASCADE, related_name="forecasts")
    model = models.CharField(max_length=32)
    origin = models.DateField(help_text="last day of data the forecast was made from")
    target = models.DateField()
    yhat = models.FloatField()
    lo = models.FloatField(help_text="80% interval lower bound")
    hi = models.FloatField(help_text="80% interval upper bound")

    class Meta:
        constraints = [
            # One forecast per day for India / each state ...
            models.UniqueConstraint(
                fields=["commodity", "state", "target"],
                condition=models.Q(market__isnull=True),
                name="uniq_forecast",
            ),
            # ... and one per day for each mandi.
            models.UniqueConstraint(
                fields=["commodity", "market", "target"],
                condition=models.Q(market__isnull=False),
                name="uniq_forecast_market",
            ),
        ]


class MarketGroup(models.Model):
    """One behaviour group found by K-means for one commodity."""

    commodity = models.CharField(max_length=96)
    number = models.SmallIntegerField()  # 0, 1, 2 ... as K-means numbered it
    label = models.CharField(max_length=64)  # e.g. "Cheap and steady"
    description = models.CharField(max_length=200)
    size = models.IntegerField()
    # average feature values of the group (its centre), in readable units
    price_vs_india_pct = models.FloatField()
    weekly_swing_pct = models.FloatField()
    trend_pct_per_week = models.FloatField()
    follows_india = models.FloatField(help_text="correlation with the all-India price, -1..1")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["commodity", "number"], name="uniq_group")]


class MarketGroupMember(models.Model):
    """Which group a mandi belongs to, and where it sits on the 2-D PCA picture."""

    commodity = models.CharField(max_length=96)
    market = models.ForeignKey(DimMarket, on_delete=models.CASCADE)
    group_number = models.SmallIntegerField()
    pca_x = models.FloatField()
    pca_y = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["commodity", "market"], name="uniq_group_member")]
