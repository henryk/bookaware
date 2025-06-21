ARG BUILD_FROM
FROM $BUILD_FROM

RUN \
  apk add --no-cache \
    python3 py3-pip py3-virtualenv

RUN python3 -m venv /venv
COPY bookaware README.md poetry.lock pyproject.toml /
RUN /venv/bin/pip install .

CMD [ "/venv/bin/python", "-m", "bookaware.main" ]
