FROM python:3.11-slim

WORKDIR /main

COPY main.py .

EXPOSE 5555

CMD ["python", "main.py"]
