FROM python:3.14-alpine

WORKDIR /app

COPY worker/requirements.txt .
ARG PIP_PROXY=""
RUN pip install --no-cache-dir ${PIP_PROXY:+--proxy "$PIP_PROXY"} -r requirements.txt

COPY worker/ .
COPY shared/ ./shared/

CMD ["python", "main.py"]
