FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_FLAG="FLAG{SSTI_JINJA2_M4ST3R_2026}"
EXPOSE 5008
CMD ["python", "app.py"]
