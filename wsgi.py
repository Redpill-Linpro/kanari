#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, render_template, jsonify, Response

import logging
import os
import statistics
import sys
import tempfile
import time
import uuid

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Dict, Any, Tuple, Optional, Callable, Union

VERSION = "1.3.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

executor = ThreadPoolExecutor(max_workers=4)
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

# S3 storage
s3_endpoint = os.getenv("S3_ENDPOINT", "")
if s3_endpoint:
    try:
        import boto3
        from botocore.exceptions import ClientError

        s3_access_key = os.getenv("S3_ACCESS_KEY", "")
        s3_secret_key = os.getenv("S3_SECRET_KEY", "")
        s3_bucket = os.getenv("S3_BUCKET", "redpill-linpro-kanari")
    except ImportError:
        logger.warning("S3 support is not available (missing boto3).")
        s3_endpoint = None


def time_operation(
    operation_func: Callable[[], Any],
) -> Callable[[], Tuple[Any, float]]:
    """Decorator to time operations and return result with elapsed time."""

    def wrapper(*args, **kwargs) -> Tuple[Any, float]:
        start = time.time()
        result = operation_func(*args, **kwargs)
        elapsed_ms = round((time.time() - start) * 1000, 2)
        return result, elapsed_ms

    return wrapper


def safe_connection_close(conn: Any) -> None:
    """Safely close a database connection."""
    if conn:
        try:
            conn.close()
        except Exception:
            pass


def db_connect(max_retries: int = 5, retry_delay: int = 2) -> Optional[Any]:
    """Try reconnecting to the MariaDB/MySQL database if the initial connection fails."""
    for attempt in range(max_retries):
        try:
            logger.info(
                f"Connecting to database {db_database} on {mysql_host}:{db_port} (attempt {attempt+1}/{max_retries})"
            )
            return mariadb.connect(
                host=mysql_host,
                user=db_user,
                password=db_password,
                database=db_database,
                port=db_port,
            )
        except mariadb.Error as e:
            logger.warning(f"Database connection attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Error connecting to MySQL / MariaDB after {max_retries} attempts: {e}"
                )
                return None


def measure_fetch_performance(
    cursor: Any, query: str, iterations: int = 10
) -> Dict[str, float]:
    """Measure database fetch performance over multiple iterations."""
    fetch_times = []
    for _ in range(iterations):
        start = time.time()
        cursor.execute(query)
        cursor.fetchone()
        fetch_times.append((time.time() - start) * 1000)

    return {
        "worst": round(max(fetch_times), 2),
        "best": round(min(fetch_times), 2),
        "avg": round(statistics.mean(fetch_times), 2),
    }


def collect_db_stats() -> Dict[str, Any]:
    """Connect to MariaDB/MySQL database and collect statistics"""
    metrics: Dict[str, Any] = {}
    conn = None

    try:
        conn, connect_time = time_operation(db_connect)()
        metrics["mysql_connect_time"] = connect_time

        if not conn:
            return {
                "mysql_error": "Could not connect to MariaDB/MySQL. Check configuration.",
                "mysql_connect_time": metrics["mysql_connect_time"],
            }

        cur = conn.cursor(dictionary=True)
        query = f"SELECT * FROM {db_table} ORDER BY (SELECT NULL) DESC LIMIT 1"

        # First fetch timing
        def first_fetch() -> Any:
            cur.execute(query)
            return cur.fetchone()

        _, first_fetch_time = time_operation(first_fetch)()
        metrics["mysql_first_fetch_time"] = first_fetch_time

        # Performance measurements
        perf = measure_fetch_performance(cur, query)
        metrics.update(
            {
                "mysql_fetch_worst": perf["worst"],
                "mysql_fetch_best": perf["best"],
                "mysql_fetch_avg": perf["avg"],
            }
        )

        def close_connections() -> None:
            cur.close()
            conn.close()

        _, close_time = time_operation(close_connections)()
        metrics["mysql_close_time"] = close_time
        conn = None

        return metrics

    except Exception as e:
        safe_connection_close(conn)
        return {
            "mysql_error": str(e),
            "mysql_connect_time": metrics.get("mysql_connect_time", 0),
        }


def collect_pg_stats() -> Dict[str, Any]:
    """Collect PostgreSQL database statistics"""
    metrics: Dict[str, Any] = {}
    conn = None

    try:

        def connect_pg() -> Any:
            return psycopg2.connect(
                host=postgres_host,
                user=pg_user,
                password=pg_password,
                dbname=pg_database,
                port=pg_port,
            )

        conn, connect_time = time_operation(connect_pg)()
        metrics["postgres_connect_time"] = connect_time

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = f"SELECT * FROM {pg_table} ORDER BY (SELECT NULL) DESC LIMIT 1"

        # First fetch timing
        def first_fetch() -> Any:
            cur.execute(query)
            return cur.fetchone()

        _, first_fetch_time = time_operation(first_fetch)()
        metrics["postgres_first_fetch_time"] = first_fetch_time

        # Performance measurements
        perf = measure_fetch_performance(cur, query)
        metrics.update(
            {
                "postgres_fetch_worst": perf["worst"],
                "postgres_fetch_best": perf["best"],
                "postgres_fetch_avg": perf["avg"],
            }
        )

        def close_connections() -> None:
            cur.close()
            conn.close()

        _, close_time = time_operation(close_connections)()
        metrics["postgres_close_time"] = close_time
        conn = None

        return metrics

    except Exception as e:
        safe_connection_close(conn)
        return {
            "postgres_error": str(e),
            "postgres_connect_time": metrics.get("postgres_connect_time", 0),
        }


def cleanup_temp_files(*file_paths: str) -> None:
    """Clean up temporary files."""
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def collect_s3_stats() -> Dict[str, Any]:
    """Collect S3 storage statistics"""
    metrics: Dict[str, Any] = {}
    temp_file_path: Optional[str] = None
    download_path: Optional[str] = None

    try:
        test_bucket_name = s3_bucket
        test_key = f"stats-test-{int(time.time())}.txt"
        test_data = b"This is a test file for S3 performance testing."

        # Initialize S3 client
        def init_s3_client() -> Any:
            return boto3.client(
                "s3",
                aws_access_key_id=s3_access_key,
                aws_secret_access_key=s3_secret_key,
                endpoint_url=s3_endpoint,
            )

        s3_client, client_init_time = time_operation(init_s3_client)()
        metrics["s3_client_init_time"] = client_init_time

        # Create test file
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = temp_file.name

        download_path = f"{temp_file_path}.download"

        # Upload timing
        def upload_file() -> None:
            return s3_client.upload_file(temp_file_path, test_bucket_name, test_key)

        _, put_time = time_operation(upload_file)()
        metrics["s3_put_time"] = put_time
        logger.info(f"Successfully uploaded test object to bucket {test_bucket_name}")

        # Download performance measurements
        get_times = []
        for _ in range(5):
            try:

                def download_file() -> None:
                    return s3_client.download_file(
                        test_bucket_name, test_key, download_path
                    )

                _, get_time = time_operation(download_file)()
                get_times.append(get_time)
            except Exception as e:
                logger.error(f"Error downloading test file: {e}")

        if get_times:
            metrics.update(
                {
                    "s3_get_worst": round(max(get_times), 2),
                    "s3_get_best": round(min(get_times), 2),
                    "s3_get_avg": round(statistics.mean(get_times), 2),
                }
            )
        else:
            metrics["s3_get_error"] = "All get operations failed"

        # Cleanup test file
        try:

            def delete_object() -> Any:
                return s3_client.delete_object(Bucket=test_bucket_name, Key=test_key)

            _, delete_time = time_operation(delete_object)()
            metrics["s3_delete_file_time"] = delete_time
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
        cleanup_temp_files(temp_file_path, download_path)


def execute_service_test(
    service_name: str, test_func: Callable[[], Dict[str, Any]], host_var: Optional[str]
) -> Dict[str, Any]:
    """Execute a service test with timeout handling."""
    if not host_var:
        return {f"{service_name}_disabled": f"{service_name.title()} not configured"}

    future = executor.submit(test_func)
    try:
        result = future.result(timeout=TIMEOUT_SECONDS)
        # Check if the result contains an error that should trigger 504
        error_keys = [
            f"{service_name}_error",
            "postgres_error",
            "mysql_error",
            "s3_error",
        ]
        if any(key in result for key in error_keys):
            error_msg = f"{service_name.title()} service failed"
            logger.error(f"Service failure detected for {service_name}")
            return {"timeout_error": error_msg}
        return result
    except TimeoutError:
        error_msg = f"{service_name.title()} service request timed out"
        logger.error(f"Timeout waiting for {service_name} service test")
        return {"timeout_error": error_msg}


def collect_all_metrics() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Collect metrics from all configured services."""
    service_configs = [
        ("mysql", collect_db_stats, mysql_host),
        ("postgres", collect_pg_stats, postgres_host),
        ("s3", collect_s3_stats, s3_endpoint),
    ]

    metrics: Dict[str, Any] = {}
    for service_name, test_func, host_var in service_configs:
        result = execute_service_test(service_name, test_func, host_var)
        if "timeout_error" in result:
            return None, result["timeout_error"]
        metrics.update(result)

    return metrics, None


@app.route("/")
def index() -> Union[str, Tuple[str, int]]:
    start_time = time.time()
    logger.info("Processing index request")

    metrics, timeout_error = collect_all_metrics()
    if timeout_error:
        return (
            render_template(
                "error.html",
                error_title="504 Gateway Timeout",
                error_message=timeout_error,
            ),
            504,
        )

    # Template rendering performance
    def render_page() -> str:
        return render_template("index.html", metrics=metrics, version=VERSION)

    _, template_time = time_operation(render_page)()
    metrics["template_read_time"] = template_time
    metrics["total_time"] = round((time.time() - start_time) * 1000, 2)

    return render_template("index.html", metrics=metrics, version=VERSION)


@app.route("/reconnect", methods=["POST"])
def reconnect() -> Union[Response, Tuple[Response, int]]:
    start_time = time.time()
    logger.info("Processing reconnect request")

    metrics, timeout_error = collect_all_metrics()
    if timeout_error:
        return jsonify({"error": "Service request timed out"}), 504

    # Template rendering performance (for timing only)
    def render_page() -> str:
        return render_template("index.html", metrics=metrics, version=VERSION)

    _, template_time = time_operation(render_page)()
    metrics["template_read_time"] = template_time
    metrics["total_time"] = round((time.time() - start_time) * 1000, 2)

    return jsonify(metrics)


@app.route("/favicon.ico")
def favicon() -> Response:
    return app.send_static_file("favicon.ico")


if __name__ == "__main__":
    logger.info(f"Starting Flask application on {http_host}:{http_port}")
    logger.info(f"MariaDB/MySQL enabled: {mysql_host is not None}")
    logger.info(f"PostgreSQL enabled: {postgres_host is not None}")
    logger.info(f"S3 enabled: {s3_endpoint is not None}")
    app.run(host=http_host, port=http_port)
