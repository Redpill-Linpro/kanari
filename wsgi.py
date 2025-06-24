#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from botocore.exceptions import ClientError
from flask import Flask, render_template, jsonify

import boto3
import logging
import os
import statistics
import sys
import tempfile
import time
import uuid

from concurrent.futures import ThreadPoolExecutor, TimeoutError

versionString = "1.2.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Executor for background service tests
executor = ThreadPoolExecutor(max_workers=4)
# Timeout for service tests in seconds
TIMEOUT_SECONDS = 3

http_host = os.getenv("HOST", "0.0.0.0")
http_port = int(os.getenv("PORT", 5000))

# MySQL / MariaDB
mysql_host = os.getenv("DB_HOST")
if mysql_host:
    try:
        import mariadb

        db_port = int(os.getenv("DB_PORT", 3306))
        db_database = os.getenv("DB_NAME", "kanari")
        db_table = os.getenv("DB_TABLE", "kanari")
        db_user = os.getenv("DB_USER", "alexander")
        db_password = os.getenv("DB_PASSWORD", "")
    except ImportError:
        logger.warning("MariaDB/MySQL support is not available (missing mariadb).")
        mysql_host = None

# PostgreSQL
postgres_host = os.getenv("PG_HOST")
if postgres_host:
    try:
        import psycopg2
        import psycopg2.extras

        pg_port = int(os.getenv("PG_PORT", 5432))
        pg_database = os.getenv("PG_DATABASE", "kanari")
        pg_table = os.getenv("PG_TABLE", "kanari")
        pg_user = os.getenv("PG_USER", "alexander")
        pg_password = os.getenv("PG_PASSWORD", "")
    except ImportError:
        logger.warning("PostgreSQL support is not available (missing psycopg2).")
        postgres_host = None

# S3 buckets
s3_access_key = os.getenv("S3_ACCESS_KEY", "")
s3_secret_key = os.getenv("S3_SECRET_KEY", "")
s3_endpoint = os.getenv("S3_ENDPOINT", "https://situla.bitbit.net")
s3_bucket = os.getenv("S3_BUCKET", "redpill-linpro-kanari")


def db_connect(max_retries=5, retry_delay=2):
    """Try reconnecting to the MariaDB/MySQL database if the initial connection fails."""
    retries = 0
    while retries < max_retries:
        try:
            logger.info(
                f"Connecting to database {db_database} on {mysql_host}:{db_port} (attempt {retries+1}/{max_retries})"
            )
            return mariadb.connect(
                host=mysql_host,
                user=db_user,
                password=db_password,
                database=db_database,
                port=db_port,
            )
        except mariadb.Error as e:
            logger.warning(f"Database connection attempt {retries+1} failed: {e}")
            retries += 1
            if retries < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Error connecting to MySQL / MariaDB after {max_retries} attempts: {e}"
                )
                return None


def collect_db_stats():
    """Connect to MariaDB/MySQL database and collect statistics"""
    if not mysql_host:
        return {"mysql_disabled": "MariaDB/MySQL not configured (DB_HOST not set)"}

    metrics = {}

    conn_start = time.time()
    conn = db_connect()
    conn_time = (time.time() - conn_start) * 1000
    metrics["mysql_connect_time"] = round(conn_time, 2)

    if not conn:
        return {
            "mysql_error": "Could not connect to MariaDB/MySQL. Check configuration.",
            "mysql_connect_time": metrics["mysql_connect_time"],
        }

    try:
        cur = conn.cursor(dictionary=True)
        query = f"SELECT * FROM {db_table} ORDER BY (SELECT NULL) DESC LIMIT 1"

        first_fetch_start = time.time()
        cur.execute(query)
        row = cur.fetchone()
        metrics["mysql_first_fetch_time"] = round(
            (time.time() - first_fetch_start) * 1000, 2
        )

        fetch_times = []
        for _ in range(10):
            fetch_start = time.time()
            cur.execute(query)
            cur.fetchone()
            fetch_times.append((time.time() - fetch_start) * 1000)

        metrics["mysql_fetch_worst"] = round(max(fetch_times), 2)
        metrics["mysql_fetch_best"] = round(min(fetch_times), 2)
        metrics["mysql_fetch_avg"] = round(statistics.mean(fetch_times), 2)

        close_start = time.time()
        cur.close()
        conn.close()
        metrics["mysql_close_time"] = round((time.time() - close_start) * 1000, 2)

        return metrics

    except Exception as e:
        if conn:
            try:
                conn.close()
            except:
                pass
        return {
            "mysql_error": str(e),
            "mysql_connect_time": metrics.get("mysql_connect_time", 0),
        }


## PostgreSQL stats collection
def collect_pg_stats():
    """Collect PostgreSQL database statistics"""
    if not postgres_host:
        return {"postgres_disabled": "PostgreSQL not configured (PG_HOST not set)"}

    metrics = {}
    conn_start = time.time()
    conn = None
    try:
        conn = psycopg2.connect(
            host=postgres_host,
            user=pg_user,
            password=pg_password,
            dbname=pg_database,
            port=pg_port,
        )
        metrics["postgres_connect_time"] = round((time.time() - conn_start) * 1000, 2)

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = f"SELECT * FROM {pg_table} ORDER BY (SELECT NULL) DESC LIMIT 1"

        first_fetch_start = time.time()
        cur.execute(query)
        cur.fetchone()
        metrics["postgres_first_fetch_time"] = round(
            (time.time() - first_fetch_start) * 1000, 2
        )

        fetch_times = []
        for _ in range(10):
            fetch_start = time.time()
            cur.execute(query)
            cur.fetchone()
            fetch_times.append((time.time() - fetch_start) * 1000)

        metrics["postgres_fetch_worst"] = round(max(fetch_times), 2)
        metrics["postgres_fetch_best"] = round(min(fetch_times), 2)
        metrics["postgres_fetch_avg"] = round(statistics.mean(fetch_times), 2)

        close_start = time.time()
        cur.close()
        conn.close()
        metrics["postgres_close_time"] = round((time.time() - close_start) * 1000, 2)
        return metrics
    except Exception as e:
        if conn:
            try:
                conn.close()
            except:
                pass
        return {
            "postgres_error": str(e),
            "postgres_connect_time": metrics.get("postgres_connect_time", 0),
        }


def collect_s3_stats():
    """Collect S3 storage statistics"""
    metrics = {}

    if not s3_access_key or not s3_secret_key:
        return {"s3_error": "S3 credentials not configured"}

    temp_file_path = None
    download_path = None

    try:
        # Use existing bucket name from configuration
        test_bucket_name = s3_bucket
        # Timestamp for test object key generation
        timestamp = int(time.time())

        s3_client_start = time.time()
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            endpoint_url=s3_endpoint,
        )
        metrics["s3_client_init_time"] = round(
            (time.time() - s3_client_start) * 1000, 2
        )

        test_data = b"This is a test file for S3 performance testing."
        test_key = f"stats-test-{timestamp}.txt"

        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = temp_file.name

        download_path = f"{temp_file_path}.download"

        put_start = time.time()
        s3_client.upload_file(temp_file_path, test_bucket_name, test_key)
        metrics["s3_put_time"] = round((time.time() - put_start) * 1000, 2)
        logger.info(f"Successfully uploaded test object to bucket {test_bucket_name}")

        get_times = []
        for _ in range(5):
            get_start = time.time()
            try:
                s3_client.download_file(test_bucket_name, test_key, download_path)
                get_times.append((time.time() - get_start) * 1000)
            except Exception as e:
                logger.error(f"Error downloading test file: {e}")
                get_times.append(0)

        if get_times and max(get_times) > 0:
            metrics["s3_get_worst"] = round(max(get_times), 2)
            metrics["s3_get_best"] = round(min(filter(lambda x: x > 0, get_times)), 2)
            valid_times = [t for t in get_times if t > 0]
            if valid_times:
                metrics["s3_get_avg"] = round(statistics.mean(valid_times), 2)
            else:
                metrics["s3_get_error"] = "All get operations failed"
        else:
            metrics["s3_get_error"] = "No successful get operations"

        delete_file_start = time.time()
        try:
            s3_client.delete_object(Bucket=test_bucket_name, Key=test_key)
            metrics["s3_delete_file_time"] = round(
                (time.time() - delete_file_start) * 1000, 2
            )
        except Exception as e:
            logger.error(f"Error deleting test file: {e}")
            metrics["s3_delete_file_error"] = str(e)

        return metrics

    except ClientError as e:
        logger.error(f"Error in S3 stats collection: {e}")
        return {
            "s3_error": str(e),
            "s3_client_init_time": metrics.get("s3_client_init_time", 0),
        }
    except Exception as e:
        logger.error(f"Unexpected error in S3 stats collection: {e}")
        return {
            "s3_error": str(e),
            "s3_client_init_time": metrics.get("s3_client_init_time", 0),
        }
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if download_path and os.path.exists(download_path):
            os.remove(download_path)


@app.route("/")
def index():
    start_time = time.time()
    logger.info("Processing index request")

    # Run service tests with timeout
    mysql_future = executor.submit(collect_db_stats)
    pg_future = executor.submit(collect_pg_stats)
    s3_future = executor.submit(collect_s3_stats)
    try:
        mysql_metrics = mysql_future.result(timeout=TIMEOUT_SECONDS)
        pg_metrics = pg_future.result(timeout=TIMEOUT_SECONDS)
        s3_metrics = s3_future.result(timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        logger.error("Timeout waiting for service tests")
        return (
            render_template(
                "error.html",
                error_title="504 Gateway Timeout",
                error_message="Service request timed out",
            ),
            504,
        )

    metrics = {**mysql_metrics, **pg_metrics, **s3_metrics}

    template_start = time.time()

    # Render the template here, just to measure the performance
    response = render_template("index.html", metrics=metrics, version=versionString)

    metrics["template_read_time"] = round((time.time() - template_start) * 1000, 2)
    metrics["total_time"] = round((time.time() - start_time) * 1000, 2)

    # Fill in the newly recorded metrics before returning
    return render_template("index.html", metrics=metrics, version=versionString)


@app.route("/reconnect", methods=["POST"])
def reconnect():
    start_time = time.time()
    logger.info("Processing reconnect request")

    # Run service tests with timeout
    mysql_future = executor.submit(collect_db_stats)
    pg_future = executor.submit(collect_pg_stats)
    s3_future = executor.submit(collect_s3_stats)
    try:
        mysql_metrics = mysql_future.result(timeout=TIMEOUT_SECONDS)
        pg_metrics = pg_future.result(timeout=TIMEOUT_SECONDS)
        s3_metrics = s3_future.result(timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        logger.error("Timeout waiting for service tests")
        return jsonify({"error": "Service request timed out"}), 504

    metrics = {**mysql_metrics, **pg_metrics, **s3_metrics}

    template_start = time.time()
    render_template("index.html", metrics=metrics, version=versionString)
    metrics["template_read_time"] = round((time.time() - template_start) * 1000, 2)

    metrics["total_time"] = round((time.time() - start_time) * 1000, 2)

    return jsonify(metrics)


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


if __name__ == "__main__":
    logger.info(f"Starting Flask application on {http_host}:{http_port}")
    logger.info(f"MariaDB/MySQL enabled: {mysql_host is not None}")
    logger.info(f"PostgreSQL enabled: {postgres_host is not None}")
    app.run(host=http_host, port=http_port)
