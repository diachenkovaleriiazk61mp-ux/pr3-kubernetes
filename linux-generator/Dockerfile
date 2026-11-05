FROM vaccinations-pr3:local
WORKDIR /probe
COPY deps/ /probe/deps/
COPY load.py probe.py ./
ENV PYTHONPATH=/probe/deps
ENTRYPOINT ["python", "-u", "probe.py"]
