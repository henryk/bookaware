ARG BUILD_FROM
FROM $BUILD_FROM

RUN \
  apk add --no-cache \
    python3 \

COPY bookaware poetry.lock pyproject.toml /
RUN pip install .

CMD [ "python", "-m", "bookaware.main" ]
