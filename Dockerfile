# Python 3.10 の公式イメージをベースにします
FROM python:3.10-slim

# タイムゾーンをJSTに設定
ENV TZ=Asia/Tokyo
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 必要なパッケージがあれば追記します (例: git)
RUN apt-get update && apt-get install -y git
RUN pip install --upgrade pip
RUN pip install eyed3
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# コンテナ内での作業ディレクトリを指定します
WORKDIR /workspace