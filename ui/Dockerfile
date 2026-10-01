FROM python:3.12-slim

WORKDIR /code

RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir streamlit plotly requests pandas python-dotenv scipy statsmodels

COPY . .

CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
