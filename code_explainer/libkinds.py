"""The outside systems a project can talk to, and the well-known libraries that mean each one.

Shared by the architecture map (which systems exist) and the function contracts (which functions touch them).
"""

from __future__ import annotations

# kind -> label
KINDS = {"database": "A database", "web-server": "Serves web requests", "web-client": "Calls web services",
         "files": "Files on disk", "processes": "Other programs", "queue": "A message queue", "cloud": "Cloud services",
         "ai": "An AI service", "command-line": "The command line", "email": "Email", "network": "Network connections"}
# library (dotted or top-level name) -> kind. Only libraries that clearly mean "talks to something outside".
LIBRARIES = {
    "database": "sqlite3 sqlalchemy psycopg2 psycopg pymysql mysql pymongo redis peewee asyncpg aiosqlite motor sqlmodel prisma "
                "django.db pg mysql2 mongodb mongoose ioredis sequelize typeorm knex better-sqlite3",
    "web-server": "flask fastapi django starlette tornado bottle sanic http.server aiohttp.web express koa fastify next "
                  "@nestjs/core @hapi/hapi",
    "web-client": "requests httpx urllib3 aiohttp urllib.request axios node-fetch got superagent",
    "files": "shutil glob tempfile fs fs-extra",
    "processes": "subprocess multiprocessing child_process execa shelljs",
    "queue": "celery kombu pika kafka confluent_kafka amqplib kafkajs bullmq",
    "cloud": "boto3 botocore google.cloud azure firebase firebase-admin aws-sdk @aws-sdk/client-s3",
    "ai": "openai anthropic google.generativeai cohere langchain transformers @anthropic-ai/sdk",
    "command-line": "click typer argparse fire commander yargs",
    "email": "smtplib nodemailer",
    "network": "socket websockets ws socket.io",
}
_KIND_OF = {lib: kind for kind, libs in LIBRARIES.items() for lib in libs.split()}


def kind_of(name: str) -> tuple[str, str] | None:
    """(kind, library) for a dotted library name, trying the longest known prefix first ("sqlalchemy.orm" -> sqlalchemy)."""
    pieces = name.split(".")
    for k in range(len(pieces), 0, -1):
        lib = ".".join(pieces[:k])
        if lib in _KIND_OF:
            return _KIND_OF[lib], lib
    return None
