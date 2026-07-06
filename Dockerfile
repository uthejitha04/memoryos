# MemoryOS cloud core (Stage B). Cloud mode = API + memory engine + AI chain;
# BLE capture stays on user devices, audio is uploaded via /ws/audio or sync.
FROM python:3.12-slim

WORKDIR /app

# ffmpeg libs for PyAV opus decode; build tools not needed (wheels)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt sentence-transformers

COPY . .

EXPOSE 8090

CMD ["python", "server.py"]
