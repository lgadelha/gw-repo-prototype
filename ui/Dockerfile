FROM python:3.12-slim

WORKDIR /code

# Install build tools for statsmodels
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Install streamlit, plotly, requests, pandas, python-dotenv, scipy, and statsmodels for statistics
RUN pip install --no-cache-dir streamlit==1.66.0 plotly==7.1.0 requests==2.34.2 pandas==3.0.6 python-dotenv==1.2.4 scipy==1.18.1 statsmodels==0.15.0

COPY . .

CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501"]