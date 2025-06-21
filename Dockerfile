ARG BUILD_FROM
FROM $BUILD_FROM

RUN \
  apk add --no-cache \
    python3 py3-pip py3-virtualenv

RUN python3 -m venv /venv
RUN mkdir /app
WORKDIR /app
COPY README.md poetry.lock pyproject.toml ./
COPY bookaware bookaware
RUN pwd && ls -lh
RUN /venv/bin/pip install .

CMD [ "/venv/bin/python", "-m", "bookaware.main" ]
