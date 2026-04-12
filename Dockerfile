FROM python:3.12-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir ".[http]"

EXPOSE 8080

CMD ["deferno-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
