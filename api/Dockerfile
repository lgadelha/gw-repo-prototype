FROM python:3.12

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt
COPY ./wait-for-it.sh /code/wait-for-it.sh

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt


COPY ./main.py /code/
COPY ./models.py /code/
COPY ./nfcore_modules.py /code/
COPY ./ml/ /code/ml/

CMD ["/code/wait-for-it.sh", "db:5432", "--", "fastapi", "run", "main.py", "--port", "80"]
