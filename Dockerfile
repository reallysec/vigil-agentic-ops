FROM python:3.12-slim

WORKDIR /app
COPY vigil/requirements.txt vigil/requirements.txt
RUN pip install --no-cache-dir -r vigil/requirements.txt

COPY . .

EXPOSE 8800
# Bootstrap Splunk (idempotent) then launch the console.
CMD ["sh", "-c", "python scripts/bootstrap.py && python vigil/ui/server.py"]
