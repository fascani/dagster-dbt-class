from io import BytesIO

import dagster as dg
import pandas as pd
import requests
from dagster_duckdb import DuckDBResource
from smart_open import open

from dagster_and_dbt.completed.lesson_2.defs.assets import constants
from dagster_and_dbt.completed.lesson_2.defs.partitions import monthly_partition
from dagster_and_dbt.completed.lesson_2.defs.resources import smart_open_config


@dg.asset(
    group_name="raw_files",
    kinds={"python"},
)
def taxi_zones_file() -> dg.MaterializeResult:
    """The raw CSV file for the taxi zones dataset. Sourced from the NYC Open Data portal."""
    raw_taxi_zones = requests.get(
        "https://community-engineering-artifacts.s3.us-west-2.amazonaws.com/dagster-university/data/taxi_zones.csv"
    )

    with open(
        constants.TAXI_ZONES_FILE_PATH, "wb", transport_params=smart_open_config
    ) as output_file:
        output_file.write(raw_taxi_zones.content)

    num_rows = len(pd.read_csv(BytesIO(raw_taxi_zones.content)))

    return dg.MaterializeResult(
        metadata={"Number of records": dg.MetadataValue.int(num_rows)}
    )


@dg.asset(
    deps=["taxi_zones_file"],
    group_name="ingested",
    kinds={"duckdb"},
)
def taxi_zones(context: dg.AssetExecutionContext, database: DuckDBResource):
    """The raw taxi zones dataset, loaded into a DuckDB database."""
    query = f"""
        create or replace table zones as (
            select
                LocationID as zone_id,
                zone,
                borough,
                the_geom as geometry
            from '{constants.TAXI_ZONES_FILE_PATH}'
        );
    """

    with database.get_connection() as conn:
        conn.execute(query)


@dg.asset(
    partitions_def=monthly_partition,
    group_name="raw_files",
    kinds={"duckdb"},
)
def taxi_trips_file(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """The raw parquet files for the taxi trips dataset. Sourced from the NYC Open Data portal."""
    partition_date_str = context.partition_key
    month_to_fetch = partition_date_str[:-3]

    raw_trips = requests.get(
        f"https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{month_to_fetch}.parquet"
    )

    with open(
        constants.TAXI_TRIPS_TEMPLATE_FILE_PATH.format(month_to_fetch),
        "wb",
        transport_params=smart_open_config,
    ) as output_file:
        output_file.write(raw_trips.content)

    num_rows = len(pd.read_parquet(BytesIO(raw_trips.content)))
    return dg.MaterializeResult(
        metadata={"Number of records": dg.MetadataValue.int(num_rows)}
    )


@dg.asset(
    deps=["taxi_trips_file"],
    partitions_def=monthly_partition,
    group_name="ingested",
    kinds={"duckdb"},
)
def taxi_trips(context: dg.AssetExecutionContext, database: DuckDBResource):
    """The raw taxi trips dataset, loaded into a DuckDB database, partitioned by month."""
    partition_date_str = context.partition_key
    month_to_fetch = partition_date_str[:-3]

    query = f"""
        create table if not exists trips (
            vendor_id integer, pickup_zone_id integer, dropoff_zone_id integer,
            rate_code_id double, payment_type integer, dropoff_datetime timestamp,
            pickup_datetime timestamp, trip_distance double, passenger_count double,
            total_amount double, partition_date varchar
        );

        delete from trips where partition_date = '{month_to_fetch}';
    
        insert into trips
        select
            VendorID, PULocationID, DOLocationID, RatecodeID, payment_type, tpep_dropoff_datetime, 
            tpep_pickup_datetime, trip_distance, passenger_count, total_amount, '{month_to_fetch}' as partition_date
        from '{constants.TAXI_TRIPS_TEMPLATE_FILE_PATH.format(month_to_fetch)}';
    """

    with database.get_connection() as conn:
        conn.execute(query)
