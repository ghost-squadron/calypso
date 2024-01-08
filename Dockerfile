FROM python:3.11
WORKDIR /app/

COPY ./.env /app/src/
COPY ./requirements.txt /app/

RUN pip install -r requirements.txt

COPY ./mypy.ini /app/
COPY ./backend/*.py /app/src/
RUN mypy src --config-file /app/mypy.ini

WORKDIR /app/src/
CMD python app.py