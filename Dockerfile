FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

RUN mkdir -p output_audio/previews uploads checkpoints

EXPOSE 7860

CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:7860", "--timeout", "1800", "app:app"]
