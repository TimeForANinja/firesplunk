FROM python:3.14-alpine

WORKDIR /app

COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker/ .
COPY shared/ ./shared/

CMD ["python", "main.py"]
