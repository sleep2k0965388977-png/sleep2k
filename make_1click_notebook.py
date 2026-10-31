import json
from pathlib import Path

notebook = {
    "cells": [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# 🚀 SLEEP2K AI CLONE WORKER (48kHz ULTRA STUDIO)\n",
                "### Bộ máy AI Sao Chép Giọng Siêu Thực 98% Chạy 1-Click Trên Google Colab GPU T4\n",
                "\n",
                "> **👉 Bạn chỉ cần bấm 1 nút Play (▶) ở ô code bên dưới là XONG!**\n",
                "> Hệ thống sẽ tự động cài đặt, tự động kết nối thông về website `https://sleep2k-1.onrender.com` 100% tự động!"
            ]
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# 🚀 SLEEP2K 1-CLICK AI WORKER (48kHz ULTRA STUDIO)\n",
                "# Tự động tải bản mới nhất & Tự động kết nối với website https://sleep2k-1.onrender.com\n",
                "!curl -sSL https://raw.githubusercontent.com/sleep2k0965388977-png/sleep2k/main/colab_voice_clone.py -o /tmp/colab_worker.py && python3 -u /tmp/colab_worker.py\n"
            ]
        }
    ],
    "metadata": {
        "accelerator": "GPU",
        "colab": {
            "gpuType": "T4",
            "provenance": []
        },
        "kernelspec": {
            "display_name": "Python 3",
            "name": "python3"
        },
        "language_info": {
            "name": "python"
        }
    },
    "nbformat": 4,
    "nbformat_minor": 0
}

with open("D:/sleep2k-deploy/sleep2k_ai_worker.ipynb", "w", encoding="utf-8") as f:
    json.dump(notebook, f, ensure_ascii=False, indent=2)

print("Updated 1-click sleep2k_ai_worker.ipynb successfully!")
