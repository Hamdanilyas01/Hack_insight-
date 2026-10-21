
from heatsafe_hourly import heatsafe_hourly

if __name__ == "__main__":
    heatsafe_hourly.serve(
        name="heatsafe-karachi-hourly",
        cron="0 * * * *",
        tags=["heatsafe", "karachi", "hourly"],
        description="Hourly Karachi weather collection, quality checks, DuckDB updates and ML inference."
    )
