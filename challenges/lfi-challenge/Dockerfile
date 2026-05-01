FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Create the flag file
RUN echo "FLAG{LFI_D1RECTORY_TR4V3RS4L_2026}" > /etc/flag.txt
EXPOSE 5006
CMD ["python", "app.py"]
