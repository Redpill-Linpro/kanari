#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from botocore.exceptions import ClientError
from flask import Flask, render_template, jsonify
import boto3
import logging
import mariadb
import os
import statistics
import sys
import tempfile
import time
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

http_host = os.getenv("HOST", "0.0.0.0")
http_port = int(os.getenv("PORT", 5000))

db_host = os.getenv("DB_HOST", "localhost")
db_port = int(os.getenv("DB_PORT", 3306))
db_database = os.getenv("DB_NAME", "kanari")
db_table = os.getenv("DB_TABLE", "kanari")
db_user = os.getenv("DB_USER", "alexander")
db_password = os.getenv("DB_PASSWORD", "")

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
                f"Connecting to database {db_database} on {db_host}:{db_port} (attempt {retries+1}/{max_retries})"
            )
            return mariadb.connect(
                host=db_host,
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
    metrics = {}

    conn_start = time.time()
    conn = db_connect()
    conn_time = (time.time() - conn_start) * 1000
    metrics["db_connect_time"] = round(conn_time, 2)

    if not conn:
        return {
            "error": "Could not connect to database. Check configuration.",
            "db_connect_time": metrics["db_connect_time"],
        }

    try:
        cur = conn.cursor(dictionary=True)
        query = f"SELECT * FROM {db_table} ORDER BY (SELECT NULL) DESC LIMIT 1"

        first_fetch_start = time.time()
        cur.execute(query)
        row = cur.fetchone()
        metrics["first_fetch_time"] = round((time.time() - first_fetch_start) * 1000, 2)

        fetch_times = []
        for _ in range(10):
            fetch_start = time.time()
            cur.execute(query)
            cur.fetchone()
            fetch_times.append((time.time() - fetch_start) * 1000)

        metrics["fetch_worst"] = round(max(fetch_times), 2)
        metrics["fetch_best"] = round(min(fetch_times), 2)
        metrics["fetch_avg"] = round(statistics.mean(fetch_times), 2)

        close_start = time.time()
        cur.close()
        conn.close()
        metrics["db_close_time"] = round((time.time() - close_start) * 1000, 2)

        return metrics

    except Exception as e:
        if conn:
            try:
                conn.close()
            except:
                pass
        return {"error": str(e), "db_connect_time": metrics.get("db_connect_time", 0)}


def collect_s3_stats():
    """Collect S3 storage statistics"""
    metrics = {}

    if not s3_access_key or not s3_secret_key:
        return {"s3_error": "S3 credentials not configured"}

    temp_file_path = None
    download_path = None

    try:
        timestamp = int(time.time())
        random_suffix = str(uuid.uuid4())[:8]
        test_bucket_name = f"kanari-stats-{timestamp}-{random_suffix}"

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

        bucket_create_start = time.time()
        try:
            s3_client.create_bucket(Bucket=test_bucket_name)
            logger.info(f"Created test bucket: {test_bucket_name}")
            metrics["s3_bucket_create_time"] = round(
                (time.time() - bucket_create_start) * 1000, 2
            )
        except Exception as e:
            logger.error(f"Failed to create test bucket: {e}")
            return {
                "s3_error": f"Could not create test bucket: {str(e)}",
                "s3_client_init_time": metrics.get("s3_client_init_time", 0),
            }


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

        delete_bucket_start = time.time()
        try:
            s3_client.delete_bucket(Bucket=test_bucket_name)
            logger.info(f"Deleted test bucket: {test_bucket_name}")
            metrics["s3_delete_bucket_time"] = round(
                (time.time() - delete_bucket_start) * 1000, 2
            )
        except Exception as e:
            logger.warning(f"Failed to delete test bucket {test_bucket_name}: {e}")
            metrics["s3_delete_bucket_error"] = str(e)

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

    db_metrics = collect_db_stats()
    s3_metrics = collect_s3_stats()

    metrics = {**db_metrics, **s3_metrics}

    template_start = time.time()
    response = render_template("index.html", metrics=metrics)
    metrics["template_read_time"] = round((time.time() - template_start) * 1000, 2)

    metrics["total_time"] = round((time.time() - start_time) * 1000, 2)

    return render_template("index.html", metrics=metrics)


@app.route("/reconnect", methods=["POST"])
def reconnect():
    start_time = time.time()
    logger.info("Processing reconnect request")

    db_metrics = collect_db_stats()
    s3_metrics = collect_s3_stats()

    metrics = {**db_metrics, **s3_metrics}

    template_start = time.time()
    render_template("index.html", metrics=metrics)
    metrics["template_read_time"] = round((time.time() - template_start) * 1000, 2)

    metrics["total_time"] = round((time.time() - start_time) * 1000, 2)

    return jsonify(metrics)


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


if __name__ == "__main__":
    logger.info(f"Starting Flask application on {http_host}:{http_port}")
    app.run(host=http_host, port=http_port)
