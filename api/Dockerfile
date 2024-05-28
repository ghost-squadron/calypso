FROM python:3.11
WORKDIR /app/

COPY ./log_conf.json /app/
COPY ./mypy.ini /app/
COPY ./entrypoint.sh /app/

COPY ./.env /app/

COPY ./requirements.txt /app/

RUN pip install -r requirements.txt

COPY ./mypy.ini /app/
COPY ./app.py /app/
RUN mypy app.py --config-file /app/mypy.ini

ENTRYPOINT [ "./entrypoint.sh" ]