FROM python:3.11-slim
WORKDIR /probe
RUN pip install --no-cache-dir aiohttp==3.12.15
COPY load.py probe.py ./
ENTRYPOINT ["python", "-u", "probe.py"]
 
