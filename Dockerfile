FROM python:3.11-slim

WORKDIR /main

COPY . .

RUN pip install websockets

CMD ["python", "main.py"]
