FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 paho-mqtt==2.1.0 requests==2.32.3 numpy onnxruntime pillow openpyxl==3.1.5 tzdata
COPY . .
CMD ["python", "-u", "app.py"]
