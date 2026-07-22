# Analysepagina voor de RTL433-ontvangst.
# Leest events.jsonl (gedeeld met de rtl433-container) en toont het rapport.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY analyse.py /app/analyse.py

EXPOSE 8000
CMD ["python", "analyse.py"]
