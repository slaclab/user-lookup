FROM tiangolo/uvicorn-gunicorn-fastapi:python3.10-slim

RUN apt-get update -y && apt-get install -y build-essential python3-dev libldap2-dev libsasl2-dev

RUN mkdir -p /app
WORKDIR /app
COPY requirements.txt /app

RUN pip3 install -r /app/requirements.txt

COPY . /app

ENTRYPOINT [ "uvicorn", "main:app", "--host", "0.0.0.0", "--reload" ]
