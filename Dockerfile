FROM python:3.12-slim
WORKDIR /app
COPY demo/server.py demo/trades.json ./
EXPOSE 8080
CMD ["python", "server.py"]
