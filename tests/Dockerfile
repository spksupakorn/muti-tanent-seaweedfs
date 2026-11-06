FROM python:3.12-slim

RUN pip install --no-cache-dir pytest httpx boto3
WORKDIR /tests/stack
CMD ["pytest", "-v", "-p", "no:cacheprovider"]
