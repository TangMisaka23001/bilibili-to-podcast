FROM python:3.12

ADD . /app

WORKDIR /app

RUN apt update && apt install ffmpeg && pip install -r requirements.txt

EXPOSE 8000

CMD [ "python" "start_up.py" ]