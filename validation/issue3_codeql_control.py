"""Intentionally unsafe, unexecuted CodeQL control on an unmerged validation branch."""
import os

from flask import Flask, request

app = Flask(__name__)


@app.route("/intentional-codeql-control")
def intentional_command_injection():
    return os.system(request.args["command"])
