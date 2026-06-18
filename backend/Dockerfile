FROM python:3.14-alpine

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create static directory to ensure it exists
RUN mkdir -p static

COPY backend/ .
COPY shared/ ./shared/
COPY frontend/ ./static/

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "app:app"]
