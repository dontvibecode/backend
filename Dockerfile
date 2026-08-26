# Use Python 3.13 slim image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies (needed for psycopg2)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Collect static files
RUN SECRET_KEY=build-time-dummy-key python manage.py collectstatic --noinput

# PaaS hosts (Koyeb, Render, etc.) inject $PORT.
CMD exec gunicorn --bind :$PORT --timeout 120 --worker-class gevent --worker-connections 1000 api.wsgi