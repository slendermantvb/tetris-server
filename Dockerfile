FROM python:3.11-slim

WORKDIR /main

COPY . .

RUN pip install websockets

EXPOSE 8000

CMD ["python", "main.py"]
