"""Thin BigQuery wrapper: read-only, cost-capped, and easy to fake in tests."""

from google.cloud import bigquery

from . import config


class Warehouse:
    """Runs read-only queries against one dataset with a byte ceiling."""

    def __init__(self, project=None, dataset=None, location=None, client=None):
        self.project = project or config.PROJECT
        self.dataset = dataset or config.DATASET
        self.location = location or config.LOCATION
        self._client = client or bigquery.Client(project=self.project)

    def render(self, sql, **fmt):
        """Substitute the dataset and any window sizes into a check query.

        Every value passed here is an int cast from config or a literal in this
        repo, never user input, so string substitution is safe. Anything
        reaching SQL from outside must go through query parameters instead.
        """
        return sql.format(
            dataset=f"`{self.project}.{self.dataset}`", **fmt
        )

    def query(self, sql, **fmt):
        """Run `sql` and return a list of dicts.

        The query is capped by `MAX_BYTES_BILLED`. BigQuery rejects the job
        outright when the estimate exceeds the cap, so a runaway scan costs
        nothing.
        """
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=config.MAX_BYTES_BILLED,
            use_legacy_sql=False,
        )
        job = self._client.query(
            self.render(sql, **fmt),
            job_config=job_config,
            location=self.location,
        )
        rows = [dict(row) for row in job.result()]
        self.last_bytes_billed = job.total_bytes_billed or 0
        return rows

    def scalar(self, sql, **fmt):
        rows = self.query(sql, **fmt)
        return rows[0] if rows else {}
