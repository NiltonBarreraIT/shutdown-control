FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir openpyxl==3.1.5
COPY app.py index.html login.html users.html plan.html app.js users.js plan.js style.css ./
COPY img ./img
RUN mkdir -p /data && chown -R 10001:10001 /data /app
USER 10001
ENV DATABASE_PATH=/data/shutdown.db HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["python", "app.py"]
