import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

app = Flask(__name__)

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Visit(db.Model):
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    visited_at = db.Column(db.DateTime, nullable=False)
    client_ip = db.Column(db.String(64), nullable=False)


with app.app_context():
    db.create_all()


@app.route("/hello", methods=["GET"])
def hello():
    visit = Visit(
        visited_at=datetime.now(),
        client_ip=request.remote_addr,
    )
    db.session.add(visit)
    db.session.commit()
    return "Hello", 200
