FROM python:3.12-slim

ENV  PYTHONDONTWRITEBYTECODE=1 \
     PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . . 

ENTRYPOINT ["python", "main.py"]
CMD ["run-all", "--universe", "upcoming", "--strategy", "upcoming_breakouts", "--top", "15"]


