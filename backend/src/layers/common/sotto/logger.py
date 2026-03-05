"""Powertools Logger, Tracer, and Metrics — single source of truth.

Every Lambda handler imports from here. Never re-instantiate in handlers.
"""

import os

from aws_lambda_powertools import Logger, Metrics, Tracer

logger = Logger(service="sotto", level=os.environ.get("LOG_LEVEL", "DEBUG"))
tracer = Tracer(service="sotto")
metrics = Metrics(namespace="Sotto", service="sotto")
