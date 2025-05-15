FROM python:3-alpine

RUN apk add --no-cache gcc mariadb-client mariadb-connector-c mariadb-dev musl-dev

RUN mkdir -p /app/kanari
WORKDIR /app/kanari

COPY static ./static/
COPY templates ./templates/
COPY requirements.txt wsgi.py ./

# Don't write .pyc files
ENV PYTHONDONTWRITEBYTECODE=1

# Flush logs immediately
ENV PYTHONUNBUFFERED=1

# No auto reload, a bit better performance and a bit better security since it does not serve debug-related pages
ENV FLASK_DEBUG=0

RUN pip3 install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD ["python3", "-m", "gunicorn", "--bind", "0.0.0.0:8080", "wsgi:app"]
