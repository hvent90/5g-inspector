# T-Mobile Gateway Prometheus Exporter
# Exports metrics from the T-Mobile 5G Gateway for Prometheus scraping

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy exporter code
COPY gateway_exporter.py .

# Run the exporter
EXPOSE 9100
CMD ["python", "gateway_exporter.py"]
