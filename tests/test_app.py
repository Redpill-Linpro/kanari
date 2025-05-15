import os
import time
import pytest
import mariadb
import psycopg2
import boto3

from wsgi import collect_db_stats, collect_pg_stats, collect_s3_stats

@pytest.fixture(scope="session", autouse=True)
def setup_services():
    # MySQL setup
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_user = os.getenv("DB_USER", "test_user")
    db_password = os.getenv("DB_PASSWORD", "test_password")
    db_name = os.getenv("DB_NAME", "kanari")

    # Wait for MySQL service
    for _ in range(30):
        try:
            conn = mariadb.connect(host=db_host, port=db_port, user=db_user, password=db_password)
            conn.close()
            break
        except mariadb.Error:
            time.sleep(2)
    # Initialize MySQL database
    conn = mariadb.connect(host=db_host, port=db_port, user=db_user, password=db_password)
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
    cur.execute(f"USE {db_name}")
    with open("sql/init.sql", "r") as f:
        sql = f.read()
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    conn.close()

    # PostgreSQL setup
    pg_host = os.getenv("PG_HOST", "localhost")
    pg_port = int(os.getenv("PG_PORT", "5432"))
    pg_user = os.getenv("PG_USER", "test_user")
    pg_password = os.getenv("PG_PASSWORD", "test_password")
    pg_database = os.getenv("PG_DATABASE", "kanari")

    # Wait for PostgreSQL service
    for _ in range(30):
        try:
            conn_pg = psycopg2.connect(host=pg_host, port=pg_port, user=pg_user, password=pg_password, dbname=pg_database)
            conn_pg.close()
            break
        except Exception:
            time.sleep(2)
    # Initialize PostgreSQL database
    conn_pg = psycopg2.connect(host=pg_host, port=pg_port, user=pg_user, password=pg_password, dbname=pg_database)
    cur_pg = conn_pg.cursor()
    with open("sql/init.sql", "r") as f:
        sql = f.read()
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            cur_pg.execute(stmt)
    conn_pg.commit()
    cur_pg.close()
    conn_pg.close()

    # Wait for S3 service
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
        endpoint_url=os.getenv("S3_ENDPOINT"),
    )
    for _ in range(30):
        try:
            s3.list_buckets()
            break
        except Exception:
            time.sleep(2)

    # Ensure test bucket exists
    bucket = os.getenv("S3_BUCKET", "redpill-linpro-kanari")
    try:
        s3.create_bucket(Bucket=bucket)
    except Exception:
        pass

    yield


def test_collect_db_stats():
    metrics = collect_db_stats()
    assert "mysql_connect_time" in metrics
    assert metrics["mysql_connect_time"] >= 0
    assert "mysql_first_fetch_time" in metrics
    assert "mysql_fetch_avg" in metrics
    assert metrics["mysql_fetch_avg"] >= 0


def test_collect_pg_stats():
    metrics = collect_pg_stats()
    assert "postgres_connect_time" in metrics
    assert metrics["postgres_connect_time"] >= 0
    assert "postgres_first_fetch_time" in metrics
    assert "postgres_fetch_avg" in metrics
    assert metrics["postgres_fetch_avg"] >= 0


def test_collect_s3_stats():
    metrics = collect_s3_stats()
    assert "s3_client_init_time" in metrics
    assert metrics["s3_client_init_time"] >= 0
    assert "s3_put_time" in metrics
    assert metrics["s3_put_time"] >= 0
    assert "s3_delete_file_time" in metrics
    assert metrics["s3_delete_file_time"] >= 0
